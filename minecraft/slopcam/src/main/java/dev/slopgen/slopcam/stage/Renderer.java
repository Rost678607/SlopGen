package dev.slopgen.slopcam.stage;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.moulberry.flashback.Flashback;
import com.moulberry.flashback.combo_options.VideoCodec;
import com.moulberry.flashback.combo_options.VideoContainer;
import com.moulberry.flashback.exporting.ExportJobQueue;
import com.moulberry.flashback.exporting.ExportSettings;
import com.moulberry.flashback.keyframe.impl.CameraKeyframe;
import com.moulberry.flashback.keyframe.impl.FOVKeyframe;
import com.moulberry.flashback.keyframe.interpolation.InterpolationType;
import com.moulberry.flashback.keyframe.types.CameraKeyframeType;
import com.moulberry.flashback.keyframe.types.FOVKeyframeType;
import com.moulberry.flashback.state.EditorScene;
import com.moulberry.flashback.state.EditorState;
import com.moulberry.flashback.state.EditorStateManager;
import com.moulberry.flashback.state.KeyframeTrack;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.Minecraft;
import net.minecraft.world.phys.Vec3;
import org.joml.Vector3d;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * Films recorded takes: opens each replay in Flashback, writes the camera keys slopgen
 * planned into its editor, exports, and brings the player back to their world.
 *
 * <p>Opening a replay CLOSES the world — that is how Flashback works — so this is done
 * in batches: one trip out, every job of the batch exported, one trip back. Shaders
 * are Flashback's business (it has its own Iris compatibility and renders frame by
 * frame); all slopcam does is switch them on or off for the batch (a draft is filmed
 * without, the final with) and put the operator's setting back afterwards.
 *
 * <p>The operator is hidden twice over: every spectator is hidden in the export, and
 * the player's own id is in Flashback's hide-during-export set. Actors' name tags are
 * hidden too.
 */
public final class Renderer {
    private static Renderer current;

    private enum Phase { OPENING, WAITING, EXPORTING, RETURNING, DONE }

    private final Deque<JsonObject> jobs = new ArrayDeque<>();
    private final JsonArray results = new JsonArray();
    private final CompletableFuture<JsonElement> done = new CompletableFuture<>();
    private final String worldId;
    private final Boolean shaders;
    private Boolean shadersBefore;
    private boolean hideGuiBefore;
    private Phase phase;
    private JsonObject job;
    private int wait;

    private Renderer(JsonArray batch, String worldId, Boolean shaders) {
        for (JsonElement e : batch) jobs.add(e.getAsJsonObject());
        this.worldId = worldId;
        this.shaders = shaders;
    }

    public static boolean busy() {
        return current != null;
    }

    /**
     * Client thread. {@code batch}: [{replay, out, start_tick, end_tick, width, height,
     * fps, keys: [{tick, pos, yaw, pitch, fov, cut}], hide: [uuid], actors: [uuid]}].
     */
    public static CompletableFuture<JsonElement> start(JsonArray batch, Boolean shaders) {
        if (current != null || Performer.busy()) return CompletableFuture.failedFuture(new IllegalStateException("the game is busy with another take"));
        String bad = FlashbackBridge.check();
        if (bad != null) return CompletableFuture.failedFuture(new IllegalStateException(bad));
        Path save = SlopcamClient.store().saveDir();
        if (save == null) return CompletableFuture.failedFuture(new IllegalStateException("open the world first: the player is brought back to it after filming"));
        if (batch.isEmpty()) return CompletableFuture.completedFuture(new JsonArray());
        if (SlopcamClient.markup().active()) SlopcamClient.markup().exit();
        Renderer r = new Renderer(batch, save.getFileName().toString(), shaders);
        current = r;
        r.hideGuiBefore = Minecraft.getInstance().options.hideGui;
        r.setShaders();
        r.next();
        return r.done;
    }

    private void next() {
        job = jobs.poll();
        if (job == null) {
            goHome();
            return;
        }
        Path replay = Path.of(job.get("replay").getAsString());
        if (!Files.isRegularFile(replay)) {
            fail("no replay at " + replay);
            next();
            return;
        }
        phase = Phase.OPENING;
        Flashback.openReplayWorld(replay);
        phase = Phase.WAITING;
        wait = 0;
    }

    /** Client tick. */
    public static void clientTick(Minecraft mc) {
        Renderer r = current;
        if (r != null) r.tick(mc);
    }

    private void tick(Minecraft mc) {
        switch (phase) {
            case WAITING -> {
                if (!Flashback.isInReplay() || mc.level == null || mc.player == null || Flashback.getReplayServer() == null) return;
                // give the chunks a moment to arrive before the first frame is taken
                if (++wait < 40) return;
                try {
                    configure(mc);
                    phase = Phase.EXPORTING;
                    wait = 0;
                } catch (Exception e) {
                    SlopcamClient.LOG.error("slopcam: cannot set the replay up", e);
                    fail("cannot set the replay up: " + e.getMessage());
                    next();
                }
            }
            case EXPORTING -> {
                wait++;
                if (wait < 10 || Flashback.EXPORT_JOB != null || !ExportJobQueue.queuedJobs.isEmpty()) return;
                Path out = Path.of(job.get("out").getAsString());
                JsonObject res = new JsonObject();
                res.addProperty("out", out.toString());
                res.addProperty("ok", Files.isRegularFile(out));
                results.add(res);
                next();
            }
            case RETURNING -> {
                Path save = SlopcamClient.store().saveDir();
                if (save != null && save.getFileName().toString().equals(worldId) && mc.player != null) {
                    phase = Phase.DONE;
                    current = null;
                    done.complete(results);
                }
            }
            default -> {
            }
        }
    }

    private void configure(Minecraft mc) {
        EditorState st = EditorStateManager.getCurrent();
        if (st == null) throw new IllegalStateException("Flashback has no editor state for this replay");
        JsonArray keys = job.getAsJsonArray("keys");
        long stamp = st.acquireWrite();
        try {
            EditorScene scene = st.getCurrentScene(stamp);
            scene.keyframeTracks.clear();
            KeyframeTrack cam = new KeyframeTrack(CameraKeyframeType.INSTANCE);
            KeyframeTrack fov = new KeyframeTrack(FOVKeyframeType.INSTANCE);
            for (JsonElement k : keys) {
                JsonObject o = k.getAsJsonObject();
                int t = o.get("tick").getAsInt();
                JsonArray p = o.getAsJsonArray("pos");
                // the last key of a shot HOLDS, so the cut to the next shot is a cut
                InterpolationType in = o.has("cut") && o.get("cut").getAsBoolean() ? InterpolationType.HOLD : InterpolationType.SMOOTH;
                cam.keyframesByTick.put(t, new CameraKeyframe(new Vector3d(p.get(0).getAsDouble(), p.get(1).getAsDouble(), p.get(2).getAsDouble()),
                        o.get("yaw").getAsFloat(), o.get("pitch").getAsFloat(), 0f, in));
                fov.keyframesByTick.put(t, new FOVKeyframe(o.get("fov").getAsFloat(), in));
            }
            scene.keyframeTracks.add(cam);
            scene.keyframeTracks.add(fov);
        } finally {
            st.release(stamp);
        }
        st.hideAllSpectators = true;
        st.hideDuringExport.add(mc.player.getUUID());
        if (job.has("hide")) for (JsonElement u : job.getAsJsonArray("hide")) st.hideDuringExport.add(UUID.fromString(u.getAsString()));
        if (job.has("actors")) for (JsonElement u : job.getAsJsonArray("actors")) st.hideNametags.add(UUID.fromString(u.getAsString()));
        st.markDirty();

        JsonObject first = keys.get(0).getAsJsonObject();
        JsonArray p0 = first.getAsJsonArray("pos");
        Path out = Path.of(job.get("out").getAsString());
        try {
            Files.createDirectories(out.getParent());
            Files.deleteIfExists(out);
        } catch (Exception ignored) {
        }
        String encoder = VideoCodec.H264.getEncoders().length > 0 ? VideoCodec.H264.getEncoders()[0] : "libx264";
        ExportSettings settings = new ExportSettings(job.has("name") ? job.get("name").getAsString() : null, st.copy(),
                new Vec3(p0.get(0).getAsDouble(), p0.get(1).getAsDouble(), p0.get(2).getAsDouble()),
                first.get("yaw").getAsFloat(), first.get("pitch").getAsFloat(),
                job.get("width").getAsInt(), job.get("height").getAsInt(),
                job.get("start_tick").getAsInt(), job.get("end_tick").getAsInt(),
                job.get("fps").getAsDouble(), true,
                VideoContainer.MP4, VideoCodec.H264, encoder, job.has("bitrate") ? job.get("bitrate").getAsInt() : 20_000_000,
                false, false, true,
                false, false, null,
                out, null);
        // the export only runs while Flashback's editor UI is hidden
        mc.options.hideGui = true;
        ExportJobQueue.queuedJobs.add(settings);
        ExportJobQueue.drainingQueue = true;
    }

    private void goHome() {
        phase = Phase.RETURNING;
        Minecraft mc = Minecraft.getInstance();
        mc.options.hideGui = hideGuiBefore;
        restoreShaders();
        if (mc.level != null) mc.level.disconnect();
        mc.disconnect();
        mc.createWorldOpenFlows().openWorld(worldId, () -> {
            current = null;
            done.completeExceptionally(new IllegalStateException("could not reopen the world " + worldId));
        });
    }

    private void fail(String why) {
        JsonObject res = new JsonObject();
        res.addProperty("out", job == null || !job.has("out") ? "" : job.get("out").getAsString());
        res.addProperty("ok", false);
        res.addProperty("error", why);
        results.add(res);
    }

    // -- Iris, by reflection: it is optional, and its API is all slopcam needs ---------

    private static Object irisConfig() {
        try {
            Class<?> api = Class.forName("net.irisshaders.iris.api.v0.IrisApi");
            Object inst = api.getMethod("getInstance").invoke(null);
            return api.getMethod("getConfig").invoke(inst);
        } catch (Throwable t) {
            return null;
        }
    }

    private void setShaders() {
        if (shaders == null) return;
        Object cfg = irisConfig();
        if (cfg == null) return;
        try {
            shadersBefore = (Boolean) cfg.getClass().getMethod("areShadersEnabled").invoke(cfg);
            if (!shaders.equals(shadersBefore)) cfg.getClass().getMethod("setShadersEnabledAndApply", boolean.class).invoke(cfg, shaders);
        } catch (Throwable t) {
            SlopcamClient.LOG.warn("slopcam: cannot switch shaders", t);
        }
    }

    private void restoreShaders() {
        if (shadersBefore == null || shadersBefore.equals(shaders)) return;
        Object cfg = irisConfig();
        if (cfg == null) return;
        try {
            cfg.getClass().getMethod("setShadersEnabledAndApply", boolean.class).invoke(cfg, shadersBefore);
        } catch (Throwable t) {
            SlopcamClient.LOG.warn("slopcam: cannot switch shaders back", t);
        }
    }
}
