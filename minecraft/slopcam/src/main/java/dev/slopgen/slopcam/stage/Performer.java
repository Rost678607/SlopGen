package dev.slopgen.slopcam.stage;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.Minecraft;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.particles.ParticleType;
import net.minecraft.core.particles.SimpleParticleType;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.TagParser;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.ButtonBlock;
import net.minecraft.world.level.block.LeverBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.Vec3;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * Plays one scene in the live world, records it with Flashback, and puts the world
 * back exactly as it was.
 *
 * <p>The order is the point:
 * <ol>
 *   <li>the player is made an invisible spectator a little above the place — the
 *       client has to be near for the chunks to load and be recorded, and must never
 *       be in the picture;</li>
 *   <li>time and weather are set, actors are placed as copies of their Easy NPC
 *       presets, tagged {@code slopgen}, AI off;</li>
 *   <li>Flashback starts recording, and the timeline runs a tick at a time on the
 *       server: events on their ticks, every puppet moved and posed, and every
 *       puppet's position written down — the camera is planned afterwards on where
 *       people actually were;</li>
 *   <li>the recording is finished under the take's name, the blocks around the take
 *       are scanned for the camera search, and everything is undone: actors removed,
 *       doors the actors opened shut again, time, weather and the player restored.</li>
 * </ol>
 * One take at a time; a second request is refused rather than queued, because two
 * takes in one world would film each other.
 */
public final class Performer {
    private static Performer current;

    private enum Phase { SETUP, STARTING, RUNNING, FINISHING, DONE }

    private final Script script;
    private final boolean record;
    private final Path replayOut;
    private final String name;
    private final CompletableFuture<JsonElement> done = new CompletableFuture<>();
    private final Map<String, Script.ActorSpec> specs = new LinkedHashMap<>();
    private final Map<String, Puppet> puppets = new LinkedHashMap<>();
    private final Map<String, JsonArray> tracks = new LinkedHashMap<>();
    private final Map<BlockPos, BlockState> touched = new HashMap<>();
    private final List<Object[]> emitters = new ArrayList<>();
    private final List<String> issues = new ArrayList<>();
    private volatile Phase phase = Phase.SETUP;
    private int tick;
    private int nextEvent;
    private ServerLevel level;
    private Navigator nav;
    // what is put back afterwards
    private UUID playerId;
    private Vec3 playerPos;
    private float playerYaw, playerPitch;
    private GameType playerMode;
    private long dayTime;
    private boolean raining, thundering;
    private int clearTime, rainTime, thunderTime;

    private Performer(Script script, boolean record, Path replayOut, String name) {
        this.script = script;
        this.record = record;
        this.replayOut = replayOut;
        this.name = name;
        for (Script.ActorSpec a : script.actors) specs.put(a.id, a);
        script.events.sort((a, b) -> Integer.compare(a.tick, b.tick));
    }

    public static boolean busy() {
        return current != null;
    }

    /** Client thread. The future completes with the take's report once the world is back. */
    public static CompletableFuture<JsonElement> start(Script script, boolean record, Path replayOut, String name) {
        if (current != null) return CompletableFuture.failedFuture(new IllegalStateException("a take is already being played"));
        Minecraft mc = Minecraft.getInstance();
        IntegratedServer server = mc.getSingleplayerServer();
        if (server == null || mc.player == null) return CompletableFuture.failedFuture(new IllegalStateException("no singleplayer world is open"));
        if (record) {
            String bad = FlashbackBridge.check();
            if (bad != null) return CompletableFuture.failedFuture(new IllegalStateException(bad));
            if (FlashbackBridge.recording()) return CompletableFuture.failedFuture(new IllegalStateException("Flashback is already recording — stop it first"));
        }
        if (SlopcamClient.markup().active()) SlopcamClient.markup().exit();
        Performer p = new Performer(script, record, replayOut, name);
        current = p;
        p.playerId = mc.player.getUUID();
        server.execute(() -> p.setup(server));
        return p.done;
    }

    public static void cancel(String why) {
        Performer p = current;
        if (p == null) return;
        p.issues.add("cancelled: " + why);
        IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
        if (p.record) FlashbackBridge.cancel();
        if (server != null) server.execute(() -> p.cleanup(null, null));
        else p.fail(new IllegalStateException(why));
    }

    // -- server thread ------------------------------------------------------------

    private void setup(MinecraftServer server) {
        try {
            ServerPlayer sp = server.getPlayerList().getPlayer(playerId);
            if (sp == null) throw new IllegalStateException("the player is gone");
            level = sp.serverLevel();
            nav = new Navigator(level);
            playerPos = sp.position();
            playerYaw = sp.getYRot();
            playerPitch = sp.getXRot();
            playerMode = sp.gameMode.getGameModeForPlayer();
            sp.setGameMode(GameType.SPECTATOR);
            sp.teleportTo(level, script.focus[0], script.focus[1] + 14, script.focus[2], 0f, 90f);

            dayTime = level.getDayTime();
            raining = level.getLevelData().isRaining();
            thundering = level.getLevelData().isThundering();
            clearTime = server.getWorldData().overworldData().getClearWeatherTime();
            rainTime = server.getWorldData().overworldData().getRainTime();
            thunderTime = server.getWorldData().overworldData().getThunderTime();
            level.setDayTime(script.time);
            switch (script.weather) {
                case "rain" -> level.setWeatherParameters(0, 6000, true, false);
                case "thunder" -> level.setWeatherParameters(0, 6000, true, true);
                default -> level.setWeatherParameters(6000, 0, false, false);
            }
            for (Script.ActorSpec a : script.actors) {
                tracks.put(a.id, new JsonArray());
                if (!a.hidden) spawn(a, new Vec3(a.pos[0], a.pos[1], a.pos[2]), a.yaw);
            }
            phase = Phase.STARTING;
            Minecraft.getInstance().execute(this::startRecording);
        } catch (Exception e) {
            SlopcamClient.LOG.error("slopcam: take setup failed", e);
            issues.add("setup failed: " + e.getMessage());
            cleanup(null, null);
        }
    }

    private void spawn(Script.ActorSpec a, Vec3 at, float yaw) {
        try {
            Path file = SlopcamClient.store().dir().resolve(a.preset);
            CompoundTag tag = TagParser.parseTag(Files.readString(file, StandardCharsets.UTF_8));
            Entity e = EntityType.loadEntityRecursive(tag, level, ent -> {
                ent.moveTo(at.x, at.y, at.z, yaw, 0f);
                ent.setUUID(UUID.randomUUID());
                return ent;
            });
            if (!(e instanceof LivingEntity le)) {
                issues.add(a.id + ": the preset of " + a.face + " is not a living entity");
                return;
            }
            le.setYHeadRot(yaw);
            le.setYBodyRot(yaw);
            le.setCustomNameVisible(false);
            if (a.scale != 1f && le.getAttribute(net.minecraft.world.entity.ai.attributes.Attributes.SCALE) != null) {
                le.getAttribute(net.minecraft.world.entity.ai.attributes.Attributes.SCALE).setBaseValue(a.scale);
            }
            for (Map.Entry<String, String> eq : a.equip.entrySet()) equip(le, eq.getKey(), eq.getValue());
            if (!level.addFreshEntity(le)) {
                issues.add(a.id + ": the world refused the actor");
                return;
            }
            puppets.put(a.id, new Puppet(a.id, le, script.bodies.get(a.id)));
            noteUuid(a.id, le.getUUID());
        } catch (Exception ex) {
            issues.add(a.id + ": cannot place " + a.face + " — " + ex.getMessage());
        }
    }

    private void equip(LivingEntity e, String slot, String item) {
        if (item == null || item.isBlank()) return;
        try {
            ItemStack stack = ItemStack.parse(level.registryAccess(), TagParser.parseTag(item)).orElse(ItemStack.EMPTY);
            EquipmentSlot s = switch (slot) {
                case "offhand" -> EquipmentSlot.OFFHAND;
                case "head" -> EquipmentSlot.HEAD;
                case "chest" -> EquipmentSlot.CHEST;
                case "legs" -> EquipmentSlot.LEGS;
                case "feet" -> EquipmentSlot.FEET;
                default -> EquipmentSlot.MAINHAND;
            };
            e.setItemSlot(s, stack);
        } catch (Exception ex) {
            issues.add("cannot read the item " + item + " — " + ex.getMessage());
        }
    }

    /** Called from the server tick (see SlopcamClient). */
    public static void serverTick(MinecraftServer server) {
        Performer p = current;
        if (p != null && p.phase == Phase.RUNNING) p.step();
    }

    private void step() {
        while (nextEvent < script.events.size() && script.events.get(nextEvent).tick <= tick) {
            apply(script.events.get(nextEvent++));
        }
        for (Puppet pu : new ArrayList<>(puppets.values())) {
            JsonObject then = pu.tick(tick, this::where);
            if (then != null) followUp(pu, then);
        }
        for (Object[] em : new ArrayList<>(emitters)) {
            if (tick > (int) em[2]) {
                emitters.remove(em);
            } else if (tick % 2 == 0) {
                Vec3 at = where((Script.Target) em[1]);
                if (at != null) level.sendParticles((SimpleParticleType) em[0], at.x, at.y, at.z, 3, 0.3, 0.3, 0.3, 0.01);
            }
        }
        for (Map.Entry<String, JsonArray> t : tracks.entrySet()) {
            Puppet pu = puppets.get(t.getKey());
            if (pu == null) {
                t.getValue().add(com.google.gson.JsonNull.INSTANCE);
                continue;
            }
            LivingEntity e = pu.entity;
            JsonArray row = new JsonArray();
            row.add(round(e.getX()));
            row.add(round(e.getY()));
            row.add(round(e.getZ()));
            row.add(round(e.getYRot()));
            row.add(round(e.getYHeadRot()));
            row.add(round(e.getXRot()));
            row.add(round(e.getEyeHeight()));
            t.getValue().add(row);
        }
        tick++;
        if (tick > script.ticks) {
            phase = Phase.FINISHING;
            Minecraft.getInstance().execute(this::finishRecording);
        }
    }

    private static double round(double v) {
        return Math.round(v * 1000) / 1000.0;
    }

    private Vec3 where(Script.Target t) {
        if (t == null) return null;
        if (t.actor != null) {
            Puppet p = puppets.get(t.actor);
            return p == null ? null : p.eyes();
        }
        return t.pos == null ? null : new Vec3(t.pos[0], t.pos[1] + 1.5, t.pos[2]);
    }

    private Vec3 feetOf(Script.Target t, Puppet self) {
        if (t == null) return null;
        if (t.actor != null) {
            Puppet o = puppets.get(t.actor);
            if (o == null) return null;
            // stop in front of the other, not inside them
            Vec3 op = o.entity.position();
            Vec3 d = self.entity.position().subtract(op);
            d = d.horizontalDistanceSqr() < 1e-4 ? new Vec3(1, 0, 0) : new Vec3(d.x, 0, d.z).normalize();
            return op.add(d.scale(1.2));
        }
        return t.pos == null ? null : new Vec3(t.pos[0], t.pos[1], t.pos[2]);
    }

    private void apply(Script.Event ev) {
        Puppet pu = puppets.get(ev.actor);
        switch (ev.op) {
            case "appear" -> {
                if (pu != null) return;
                Script.ActorSpec a = specs.get(ev.actor);
                if (a == null) return;
                Vec3 at = ev.pos != null ? new Vec3(ev.pos[0], ev.pos[1], ev.pos[2]) : new Vec3(a.pos[0], a.pos[1], a.pos[2]);
                spawn(a, at, a.yaw);
            }
            case "vanish" -> {
                if (pu != null) {
                    pu.remove();
                    puppets.remove(ev.actor);
                }
            }
            case "particles" -> {
                ParticleType<?> type = BuiltInRegistries.PARTICLE_TYPE.get(ResourceLocation.tryParse(ev.name.contains(":") ? ev.name : "minecraft:" + ev.name));
                if (type instanceof SimpleParticleType s) {
                    emitters.add(new Object[]{s, ev.target, ev.until == null ? tick + 20 : ev.until});
                } else {
                    issues.add("no simple particle " + ev.name);
                }
            }
            default -> {
                if (pu == null) return;
                switch (ev.op) {
                    case "navigate" -> {
                        Vec3 to = feetOf(ev.target, pu);
                        if (to == null) return;
                        List<Vec3> route = new ArrayList<>();
                        Vec3 from = pu.entity.position();
                        for (double[] v : ev.via) {
                            Vec3 w = new Vec3(v[0], v[1], v[2]);
                            route.addAll(nav.path(from, w));
                            from = w;
                        }
                        route.addAll(nav.path(from, to));
                        pu.walk(route, ev.speed, ev.then);
                    }
                    case "turn" -> pu.turn(ev.target);
                    case "look" -> pu.look(ev.target, ev.until);
                    case "equip" -> equip(pu.entity, ev.slot, ev.item);
                    case "use" -> use(pu, ev.target);
                    case "place" -> {
                        if (ev.pos != null) pu.entity.setPos(ev.pos[0], ev.pos[1], ev.pos[2]);
                    }
                    default -> issues.add("unknown op " + ev.op);
                }
            }
        }
    }

    private void followUp(Puppet pu, JsonObject then) {
        String op = then.has("op") ? then.get("op").getAsString() : "";
        if (op.equals("vanish")) {
            pu.remove();
            puppets.remove(pu.id);
        } else if (op.equals("place") && then.has("pos")) {
            JsonArray p = then.getAsJsonArray("pos");
            pu.entity.setPos(p.get(0).getAsDouble(), p.get(1).getAsDouble(), p.get(2).getAsDouble());
            if (then.has("yaw")) pu.face(then.get("yaw").getAsFloat());
        }
    }

    /** Open a door, press a button, pull a lever — and remember the block, to put it back. */
    private void use(Puppet pu, Script.Target t) {
        if (t == null || t.pos == null) return;
        BlockPos at = BlockPos.containing(t.pos[0], t.pos[1], t.pos[2]);
        BlockPos hit = null;
        for (BlockPos c : new BlockPos[]{at, at.above(), at.below()}) {
            BlockState s = level.getBlockState(c);
            if (s.hasProperty(BlockStateProperties.OPEN) || s.getBlock() instanceof ButtonBlock || s.getBlock() instanceof LeverBlock) {
                hit = c;
                break;
            }
        }
        pu.entity.swing(InteractionHand.MAIN_HAND, true);
        if (hit == null) {
            issues.add(pu.id + ": nothing to use at " + at.toShortString());
            return;
        }
        BlockState s = level.getBlockState(hit);
        touched.putIfAbsent(hit.immutable(), s);
        if (s.hasProperty(BlockStateProperties.OPEN)) {
            level.setBlock(hit, s.setValue(BlockStateProperties.OPEN, !s.getValue(BlockStateProperties.OPEN)), 10);
            // a door is two blocks; the other half follows on its own neighbour update
        } else if (s.getBlock() instanceof ButtonBlock b) {
            b.press(s, level, hit, null);
        } else if (s.getBlock() instanceof LeverBlock l) {
            l.pull(s, level, hit, null);
        }
    }

    // -- client thread --------------------------------------------------------------

    private void startRecording() {
        try {
            if (record) FlashbackBridge.start();
            tick = 0;
            phase = Phase.RUNNING;
        } catch (Exception e) {
            issues.add("recording did not start: " + e.getMessage());
            IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
            if (server != null) server.execute(() -> cleanup(null, null));
        }
    }

    private void finishRecording() {
        Path replay = null;
        String error = null;
        if (record) {
            try {
                replay = FlashbackBridge.finish(replayOut, name);
            } catch (Exception e) {
                error = "the recording could not be saved: " + e.getMessage();
            }
        }
        Path r = replay;
        String err = error;
        IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
        if (server != null) server.execute(() -> cleanup(r, err));
        else fail(new IllegalStateException("the world closed during the take"));
    }

    // -- server thread, last ------------------------------------------------------------

    private void cleanup(Path replay, String error) {
        JsonObject world = null;
        try {
            if (level != null) world = scan();
        } catch (Exception e) {
            issues.add("world scan failed: " + e.getMessage());
        }
        for (Puppet p : puppets.values()) p.remove();
        puppets.clear();
        if (level != null) {
            for (Map.Entry<BlockPos, BlockState> b : touched.entrySet()) level.setBlock(b.getKey(), b.getValue(), 10);
            level.setDayTime(dayTime);
            level.setWeatherParameters(clearTime, raining ? rainTime : 0, raining, thundering);
            MinecraftServer server = level.getServer();
            ServerPlayer sp = server.getPlayerList().getPlayer(playerId);
            if (sp != null && playerPos != null) {
                sp.teleportTo(level, playerPos.x, playerPos.y, playerPos.z, playerYaw, playerPitch);
                if (playerMode != null) sp.setGameMode(playerMode);
            }
        }
        if (error != null) issues.add(error);
        JsonObject report = new JsonObject();
        report.addProperty("scene", script.scene);
        report.addProperty("ticks", script.ticks);
        report.addProperty("preroll", script.preroll);
        report.addProperty("replay", replay == null ? null : replay.toString());
        report.addProperty("player", playerId.toString());
        JsonObject actors = new JsonObject();
        for (Map.Entry<String, JsonArray> t : tracks.entrySet()) {
            JsonObject a = new JsonObject();
            Script.ActorSpec spec = specs.get(t.getKey());
            a.addProperty("face", spec == null ? "" : spec.face);
            a.addProperty("extra", spec != null && spec.extra);
            a.add("track", t.getValue());
            actors.add(t.getKey(), a);
        }
        report.add("actors", actors);
        report.add("uuids", uuids);
        report.add("world", world);
        JsonArray is = new JsonArray();
        issues.forEach(is::add);
        report.add("issues", is);
        phase = Phase.DONE;
        current = null;
        done.complete(report);
    }

    private final JsonObject uuids = new JsonObject();

    private JsonObject scan() {
        double x0 = script.focus[0], y0 = script.focus[1], z0 = script.focus[2], x1 = x0, y1 = y0, z1 = z0;
        for (JsonArray tr : tracks.values()) {
            for (JsonElement row : tr) {
                if (!row.isJsonArray()) continue;
                JsonArray r = row.getAsJsonArray();
                x0 = Math.min(x0, r.get(0).getAsDouble());
                x1 = Math.max(x1, r.get(0).getAsDouble());
                y0 = Math.min(y0, r.get(1).getAsDouble());
                y1 = Math.max(y1, r.get(1).getAsDouble());
                z0 = Math.min(z0, r.get(2).getAsDouble());
                z1 = Math.max(z1, r.get(2).getAsDouble());
            }
        }
        int m = 14;
        BlockPos min = BlockPos.containing(x0 - m, y0 - 4, z0 - m), max = BlockPos.containing(x1 + m, y1 + 10, z1 + m);
        // the camera search does not need more than this, and a runaway route must not
        // turn the scan into a world download
        max = new BlockPos(Math.min(max.getX(), min.getX() + 127), Math.min(max.getY(), min.getY() + 47), Math.min(max.getZ(), min.getZ() + 127));
        return WorldScan.scan(level, min, max);
    }

    private void fail(Throwable e) {
        current = null;
        done.completeExceptionally(e);
    }

    /** The take's actors' entity ids, recorded as they spawn — the camera tracks them by these in the replay. */
    void noteUuid(String id, UUID uuid) {
        uuids.addProperty(id, uuid.toString());
    }
}
