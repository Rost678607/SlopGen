package dev.slopgen.slopcam;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import dev.slopgen.slopcam.markup.MarkupHud;
import dev.slopgen.slopcam.markup.MarkupMode;
import dev.slopgen.slopcam.markup.MarkupRenderer;
import dev.slopgen.slopcam.markup.Shots;
import dev.slopgen.slopcam.net.ControlServer;
import dev.slopgen.slopcam.net.Events;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import dev.slopgen.slopcam.stage.Performer;
import dev.slopgen.slopcam.stage.Renderer;
import dev.slopgen.slopcam.stage.Script;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.rendering.v1.HudRenderCallback;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.concurrent.CompletableFuture;

/**
 * slopcam: the part of slopgen that lives inside Minecraft.
 *
 * <p>It does two jobs. It lets the operator MARK UP the world — places, zones, points,
 * lines, cameras, the characters' faces and props — in a markup mode entered from the
 * pause menu. And it lets slopgen, connected over a localhost socket, read that markup
 * and (in later stages) stage, play, record and film machinima with Easy NPC actors
 * and Flashback.
 */
public final class SlopcamClient implements ClientModInitializer {
    public static final Logger LOG = LoggerFactory.getLogger("slopcam");

    private static SlopcamConfig config;
    private static ControlServer control;
    private static RegistryStore store;
    private static MarkupMode markup;
    private static Shots shots;
    private static final MarkupRenderer renderer = new MarkupRenderer();
    private static Path lastWorld;

    public static SlopcamConfig config() {
        return config;
    }

    public static ControlServer control() {
        return control;
    }

    public static Events events() {
        return control == null ? Events.NONE : control;
    }

    public static MarkupMode markup() {
        return markup;
    }

    public static Shots shots() {
        return shots;
    }

    public static RegistryStore store() {
        return store;
    }

    @Override
    public void onInitializeClient() {
        config = SlopcamConfig.load();
        store = new RegistryStore();
        markup = new MarkupMode(store);
        shots = new Shots();
        control = new ControlServer(config.port);
        registerApi();
        control.start();

        ClientTickEvents.START_CLIENT_TICK.register(mc -> {
            store.tick();
            if (store.saveDir() == null ? lastWorld != null : !store.saveDir().equals(lastWorld)) {
                lastWorld = store.saveDir();
                markup.worldChanged();
                // a world left while in the markup mode gets its owner's game mode back
                if (lastWorld != null) {
                    markup.restoreGameMode(mc);
                    removeLeftovers(mc);
                }
            }
            markup.tick(mc);
            Renderer.clientTick(mc);
        });
        ServerTickEvents.END_SERVER_TICK.register(Performer::serverTick);
        HudRenderCallback.EVENT.register((g, delta) -> MarkupHud.render(g, markup, store));
        WorldRenderEvents.LAST.register(ctx -> renderer.render(ctx, markup, store, shots));
        WorldRenderEvents.END.register(ctx -> shots.endOfWorld());

        ScreenEvents.AFTER_INIT.register((mc, screen, w, h) -> {
            if (!(screen instanceof PauseScreen) || mc.getSingleplayerServer() == null) return;
            Component label = Component.translatable(markup.active() ? "slopcam.pause.leave" : "slopcam.pause.enter");
            Screens.getButtons(screen).add(Button.builder(label, b -> {
                mc.setScreen(null);
                markup.toggle();
            }).bounds(4, 4, 140, 20).build());
        });
    }

    private static void registerApi() {
        control.handleNow("hello", p -> hello());
        control.handleNow("status", p -> hello());
        control.handleNow("registry.get", p -> Json.GSON.toJsonTree(store.get()));
        control.handleNow("registry.put", p -> {
            if (store.saveDir() == null) throw new IllegalStateException("no world is open");
            Registry r = Json.GSON.fromJson(p.get("registry"), Registry.class);
            if (r == null) throw new IllegalArgumentException("no registry given");
            store.replace(r);
            return Json.GSON.toJsonTree(store.get());
        });
        control.handle("perform", p -> {
            if (store.saveDir() == null) throw new IllegalStateException("no world is open");
            Script script = Json.GSON.fromJson(p.get("script"), Script.class);
            boolean record = !p.has("record") || p.get("record").getAsBoolean();
            String name = p.has("name") ? p.get("name").getAsString() : "take";
            Path out = p.has("out") ? Path.of(p.get("out").getAsString())
                    : store.dir().resolve("takes").resolve(name.replaceAll("[^\\w.-]", "_") + ".zip");
            return Performer.start(script, record, out, name);
        });
        control.handle("render", p -> {
            Boolean shaders = p.has("shaders") && !p.get("shaders").isJsonNull() ? p.get("shaders").getAsBoolean() : null;
            return Renderer.start(p.getAsJsonArray("jobs"), shaders);
        });
        control.handleNow("perform.cancel", p -> {
            Performer.cancel(p.has("why") ? p.get("why").getAsString() : "asked to");
            return null;
        });
        control.handle("markup.state", p -> {
            JsonObject o = new JsonObject();
            o.addProperty("active", markup.active());
            o.addProperty("place", markup.placeId());
            o.addProperty("tool", markup.tool().key);
            o.addProperty("selected", markup.selectedId());
            return CompletableFuture.completedFuture(o);
        });
    }

    /**
     * Actors a crashed take left behind. Everything a take spawns is tagged, so a game
     * that died mid-take does not leave a cook standing in the canteen for ever.
     */
    private static void removeLeftovers(Minecraft mc) {
        var server = mc.getSingleplayerServer();
        if (server == null) return;
        server.execute(() -> {
            for (var level : server.getAllLevels()) {
                for (var e : level.getAllEntities()) {
                    if (e.getTags().contains("slopgen") && !(e instanceof net.minecraft.world.entity.player.Player)) e.discard();
                }
            }
        });
    }

    private static JsonElement hello() {
        Minecraft mc = Minecraft.getInstance();
        JsonObject o = new JsonObject();
        o.addProperty("mod", "slopcam");
        o.addProperty("version", version("slopcam"));
        o.addProperty("minecraft", version("minecraft"));
        o.addProperty("flashback", version("flashback"));
        o.addProperty("easy_npc", version("easy_npc"));
        o.addProperty("in_world", store.saveDir() != null);
        o.addProperty("save_dir", store.saveDir() == null ? null : store.saveDir().toString());
        o.addProperty("world", store.saveDir() == null ? null : store.saveDir().getFileName().toString());
        o.addProperty("markup", markup.active());
        o.addProperty("busy", Performer.busy() || Renderer.busy());
        o.addProperty("player", mc.player == null ? null : mc.player.getGameProfile().getName());
        return o;
    }

    private static String version(String id) {
        return FabricLoader.getInstance().getModContainer(id)
                .map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse(null);
    }
}
