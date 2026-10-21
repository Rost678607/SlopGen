package dev.slopgen.slopcam.markup;

import com.mojang.blaze3d.platform.InputConstants;
import dev.slopgen.slopcam.Json;
import dev.slopgen.slopcam.SlopcamClient;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import dev.slopgen.slopcam.ui.ActorScreen;
import dev.slopgen.slopcam.ui.MarkupScreen;
import dev.slopgen.slopcam.ui.PropertiesScreen;
import net.minecraft.client.Minecraft;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.GameType;
import org.lwjgl.glfw.GLFW;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * The markup mode: entered and left by a button in the pause menu, never by a hotkey
 * — right Shift belongs to Axiom and any key of ours would collide with something.
 *
 * <p>Inside it the player flies like a spectator, through walls (the integrated
 * server really does make them one — that is also what keeps chunks loading around
 * them), the hotbar is nine markup tools the mod draws itself, and E opens the
 * markup menu instead of the inventory. Leaving restores the game mode the player
 * came with and leaves them wherever they flew to.
 *
 * <p>The game mode the player came with is written into the save before switching:
 * a game closed while in this mode would otherwise reopen with its owner stuck in
 * spectator, and nothing would remember what to give back.
 */
public final class MarkupMode {
    private static final String STATE_FILE = "markup_state.json";

    private final RegistryStore store;
    private boolean active;
    private Tool tool = Tool.SELECT;
    private String placeId = "";
    private String selectedId = "";

    // what a tool is in the middle of drawing
    private BlockPos boxCorner;
    private final List<double[]> polygon = new ArrayList<>();
    private int polygonY;
    private final List<double[]> linePoints = new ArrayList<>();
    private Registry.Piece stroke;
    private final Set<Long> ghosts = new HashSet<>();
    private boolean undoWasDown;
    private boolean deleteWasDown;

    public MarkupMode(RegistryStore store) {
        this.store = store;
    }

    public boolean active() {
        return active;
    }

    public Tool tool() {
        return tool;
    }

    public String placeId() {
        return placeId;
    }

    public Registry.Place place() {
        return store.get().place(placeId);
    }

    public void setPlace(String id) {
        placeId = id == null ? "" : id;
    }

    public String selectedId() {
        return selectedId;
    }

    public void select(String id) {
        selectedId = id == null ? "" : id;
    }

    public BlockPos boxCorner() {
        return boxCorner;
    }

    public List<double[]> polygonDraft() {
        return polygon;
    }

    public int polygonY() {
        return polygonY;
    }

    public List<double[]> lineDraft() {
        return linePoints;
    }

    public Set<Long> ghosts() {
        return ghosts;
    }

    // -- entering and leaving -------------------------------------------------

    public void toggle() {
        if (active) exit();
        else enter();
    }

    public void enter() {
        Minecraft mc = Minecraft.getInstance();
        IntegratedServer server = mc.getSingleplayerServer();
        if (server == null || mc.player == null) {
            message("slopcam.msg.singleplayer_only");
            return;
        }
        Registry reg = store.get();
        if (store.get().place(placeId) == null) {
            if (reg.places.isEmpty()) {
                store.checkpoint();
                Registry.Place p = new Registry.Place();
                p.name = Component.translatable("slopcam.default.place").getString();
                reg.places.add(p);
                store.write();
            }
            placeId = reg.places.get(0).id;
        }
        UUID id = mc.player.getUUID();
        Path state = stateFile();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(id);
            if (sp == null) return;
            GameType before = sp.gameMode.getGameModeForPlayer();
            if (before != GameType.SPECTATOR) writeState(state, before);
            sp.setGameMode(GameType.SPECTATOR);
        });
        active = true;
        tool = Tool.SELECT;
        message("slopcam.msg.entered");
    }

    public void exit() {
        Minecraft mc = Minecraft.getInstance();
        cancelDrafts();
        active = false;
        restoreGameMode(mc);
    }

    /** Give back the game mode written into the save, if one is there — on leaving the mode, and on joining a world that was left while in it. */
    public void restoreGameMode(Minecraft mc) {
        IntegratedServer server = mc.getSingleplayerServer();
        Path state = stateFile();
        if (server == null || mc.player == null || state == null || !Files.isRegularFile(state)) return;
        GameType back;
        try {
            back = GameType.byName(Json.GSON.fromJson(Files.readString(state, StandardCharsets.UTF_8), String.class), GameType.CREATIVE);
        } catch (Exception e) {
            back = GameType.CREATIVE;
        }
        GameType mode = back;
        UUID id = mc.player.getUUID();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(id);
            if (sp != null) sp.setGameMode(mode);
        });
        try {
            Files.deleteIfExists(state);
        } catch (Exception ignored) {
        }
    }

    private Path stateFile() {
        Path dir = store.dir();
        return dir == null ? null : dir.resolve(STATE_FILE);
    }

    private static void writeState(Path state, GameType mode) {
        if (state == null) return;
        try {
            Files.createDirectories(state.getParent());
            Files.writeString(state, Json.GSON.toJson(mode.getName()), StandardCharsets.UTF_8);
        } catch (Exception e) {
            SlopcamClient.LOG.warn("slopcam: cannot remember the game mode", e);
        }
    }

    /** The world changed under us: the mode does not follow the player out of it. */
    public void worldChanged() {
        active = false;
        cancelDrafts();
        selectedId = "";
        placeId = "";
        ghosts.clear();
    }

    private void cancelDrafts() {
        boxCorner = null;
        polygon.clear();
        linePoints.clear();
        stroke = null;
    }

    // -- input ----------------------------------------------------------------

    /**
     * Runs at the start of every client tick, BEFORE vanilla reads its keybinds — so
     * the clicks and keys taken here never reach the spectator's own handling, which
     * would otherwise spectate whatever was clicked or open its teleport menu.
     */
    public void tick(Minecraft mc) {
        if (!active) return;
        if (mc.player == null || mc.getSingleplayerServer() == null) {
            worldChanged();
            return;
        }
        if (mc.screen != null) return;
        var o = mc.options;
        for (int i = 0; i < 9; i++) {
            while (o.keyHotbarSlots[i].consumeClick()) setTool(Tool.values()[i]);
        }
        while (o.keyInventory.consumeClick()) mc.setScreen(new MarkupScreen(this, store));
        while (o.keyPickItem.consumeClick()) {
        }
        while (o.keySwapOffhand.consumeClick()) {
        }
        while (o.keyDrop.consumeClick()) {
        }
        boolean shift = hasShift(mc);
        while (o.keyAttack.consumeClick()) primary(mc, shift);
        while (o.keyUse.consumeClick()) secondary(mc, shift);
        if (tool == Tool.BRUSH) brush(o.keyAttack.isDown(), shift);

        long w = mc.getWindow().getWindow();
        boolean ctrl = InputConstants.isKeyDown(w, GLFW.GLFW_KEY_LEFT_CONTROL) || InputConstants.isKeyDown(w, GLFW.GLFW_KEY_RIGHT_CONTROL);
        boolean z = ctrl && InputConstants.isKeyDown(w, GLFW.GLFW_KEY_Z);
        if (z && !undoWasDown) message(store.undo() ? "slopcam.msg.undone" : "slopcam.msg.nothing_to_undo");
        undoWasDown = z;
        boolean del = InputConstants.isKeyDown(w, GLFW.GLFW_KEY_DELETE);
        if (del && !deleteWasDown) deleteSelected();
        deleteWasDown = del;
    }

    public void scroll(double dy) {
        int n = Tool.values().length;
        int i = Math.floorMod(tool.ordinal() - (int) Math.signum(dy), n);
        setTool(Tool.values()[i]);
    }

    public void setTool(Tool t) {
        if (t != tool) cancelDrafts();
        tool = t;
    }

    private static boolean hasShift(Minecraft mc) {
        long w = mc.getWindow().getWindow();
        return InputConstants.isKeyDown(w, GLFW.GLFW_KEY_LEFT_SHIFT) || InputConstants.isKeyDown(w, GLFW.GLFW_KEY_RIGHT_SHIFT);
    }

    // -- the tools ------------------------------------------------------------

    private void primary(Minecraft mc, boolean shift) {
        Registry reg = store.get();
        switch (tool) {
            case SELECT -> {
                Registry.Element e = Pick.element(reg);
                selectedId = e == null ? "" : e.id;
                if (e != null && !e.place.isEmpty()) placeId = e.place;
            }
            case BOX -> {
                BlockPos f = Pick.feet();
                if (f == null) return;
                if (boxCorner == null) {
                    boxCorner = f;
                    message("slopcam.msg.box_second");
                    return;
                }
                Registry.Piece p = new Registry.Piece();
                p.kind = "box";
                p.op = shift ? "cut" : "add";
                p.a = new int[]{boxCorner.getX(), boxCorner.getY(), boxCorner.getZ()};
                p.b = new int[]{f.getX(), f.getY(), f.getZ()};
                boxCorner = null;
                addPiece(p, shift);
            }
            case POLYGON -> {
                BlockPos f = Pick.feet();
                if (f == null) return;
                if (polygon.isEmpty()) polygonY = f.getY();
                polygon.add(new double[]{f.getX() + 0.5, f.getZ() + 0.5});
            }
            case BRUSH -> {
            }
            case LINE -> {
                BlockPos f = Pick.feet();
                if (f == null) return;
                linePoints.add(new double[]{f.getX() + 0.5, f.getY(), f.getZ() + 0.5});
            }
            case POINT -> {
                BlockPos f = Pick.feet();
                if (f == null) return;
                store.checkpoint();
                Registry.Point p = new Registry.Point();
                p.place = placeId;
                p.name = reg.freeName(placeId, Component.translatable("slopcam.default.point").getString());
                p.pos = new double[]{f.getX() + 0.5, f.getY(), f.getZ() + 0.5};
                p.yaw = Math.round(mc.player.getYRot() / 5f) * 5f;
                reg.points.add(p);
                store.write();
                selectedId = p.id;
                mc.setScreen(new PropertiesScreen(this, store, p));
            }
            case CAMERA -> {
                store.checkpoint();
                Registry.Camera c = new Registry.Camera();
                c.place = placeId;
                c.name = reg.freeName(placeId, Component.translatable("slopcam.default.camera").getString());
                aimCamera(mc, c);
                reg.cameras.add(c);
                store.write();
                selectedId = c.id;
                mc.setScreen(new PropertiesScreen(this, store, c));
            }
            case PROPERTIES -> {
                // an NPC in the crosshair wins over the markup behind it: the markup
                // is drawn on the floor it stands on
                LivingEntity npc = Pick.living();
                if (npc != null) {
                    mc.setScreen(new ActorScreen(store, npc));
                    return;
                }
                Registry.Element e = Pick.element(reg);
                if (e != null) {
                    selectedId = e.id;
                    mc.setScreen(new PropertiesScreen(this, store, e));
                }
            }
            case CHECK -> {
                Registry.Element e = Pick.element(reg);
                if (e instanceof Registry.Point p) {
                    long k = Registry.pack((int) Math.floor(p.pos[0]), (int) Math.floor(p.pos[1]), (int) Math.floor(p.pos[2]));
                    if (!ghosts.remove(k)) ghosts.add(k);
                }
            }
        }
    }

    private void secondary(Minecraft mc, boolean shift) {
        Registry reg = store.get();
        switch (tool) {
            case SELECT -> {
                Registry.Element e = reg.byId(selectedId);
                if (e instanceof Registry.Point p) {
                    BlockPos f = Pick.feet();
                    if (f == null) return;
                    store.checkpoint();
                    p.pos = new double[]{f.getX() + 0.5, f.getY(), f.getZ() + 0.5};
                    p.yaw = Math.round(mc.player.getYRot() / 5f) * 5f;
                    store.write();
                } else if (e instanceof Registry.Camera c) {
                    store.checkpoint();
                    aimCamera(mc, c);
                    store.write();
                }
            }
            case BOX -> boxCorner = null;
            case POLYGON -> {
                if (polygon.size() < 3) {
                    polygon.clear();
                    return;
                }
                Registry.Piece p = new Registry.Piece();
                p.kind = "polygon";
                p.op = shift ? "cut" : "add";
                p.points = new ArrayList<>(polygon);
                p.y0 = polygonY;
                p.y1 = polygonY + Math.max(1, SlopcamClient.config().polygonHeight) - 1;
                polygon.clear();
                addPiece(p, shift);
            }
            case LINE -> {
                if (linePoints.size() < 2) {
                    linePoints.clear();
                    return;
                }
                store.checkpoint();
                Registry.Line l = new Registry.Line();
                l.place = placeId;
                l.name = reg.freeName(placeId, Component.translatable("slopcam.default.line").getString());
                l.points = new ArrayList<>(linePoints);
                linePoints.clear();
                reg.lines.add(l);
                store.write();
                selectedId = l.id;
                mc.setScreen(new PropertiesScreen(this, store, l));
            }
            case CHECK -> ghosts.clear();
            default -> {
            }
        }
    }

    private void aimCamera(Minecraft mc, Registry.Camera c) {
        var cam = mc.gameRenderer.getMainCamera();
        c.pos = new double[]{cam.getPosition().x, cam.getPosition().y, cam.getPosition().z};
        c.yaw = cam.getYRot();
        c.pitch = cam.getXRot();
        c.fov = mc.options.fov().get();
        c.screenshot = "shots/" + c.id + ".png";
        SlopcamClient.shots().request(store.dir().resolve(c.screenshot), null);
    }

    /** The area pieces go into: the selected one if it is an area, else a new one (only for adding). */
    private void addPiece(Registry.Piece p, boolean cut) {
        Registry reg = store.get();
        Registry.Element sel = reg.byId(selectedId);
        Registry.Area area = sel instanceof Registry.Area a ? a : null;
        if (area == null && cut) {
            message("slopcam.msg.select_area_to_cut");
            return;
        }
        store.checkpoint();
        if (area == null) {
            area = new Registry.Area();
            area.place = placeId;
            area.name = reg.freeName(placeId, Component.translatable("slopcam.default.area").getString());
            reg.areas.add(area);
            selectedId = area.id;
        }
        area.pieces.add(p);
        store.write();
    }

    private void brush(boolean down, boolean shift) {
        if (!down) {
            if (stroke != null) {
                stroke = null;
                store.write();
            }
            return;
        }
        BlockPos f = Pick.feet();
        if (f == null) return;
        Registry reg = store.get();
        if (stroke == null) {
            Registry.Element sel = reg.byId(selectedId);
            if (!(sel instanceof Registry.Area) && shift) {
                message("slopcam.msg.select_area_to_cut");
                return;
            }
            store.checkpoint();
            Registry.Area area;
            if (sel instanceof Registry.Area a) {
                area = a;
            } else {
                area = new Registry.Area();
                area.place = placeId;
                area.name = reg.freeName(placeId, Component.translatable("slopcam.default.area").getString());
                reg.areas.add(area);
                selectedId = area.id;
            }
            stroke = new Registry.Piece();
            stroke.kind = "cells";
            stroke.op = shift ? "cut" : "add";
            area.pieces.add(stroke);
        }
        int r = Math.max(0, SlopcamClient.config().brushRadius);
        for (int dx = -r; dx <= r; dx++) {
            for (int dz = -r; dz <= r; dz++) {
                int x = f.getX() + dx, y = f.getY(), z = f.getZ() + dz;
                if (!stroke.contains(x, y, z)) {
                    stroke.cells.add(new int[]{x, y, z});
                    stroke.touched();
                    store.bump();
                }
            }
        }
    }

    private void deleteSelected() {
        Registry reg = store.get();
        Registry.Element e = reg.byId(selectedId);
        if (e == null) return;
        store.checkpoint();
        reg.remove(e);
        selectedId = "";
        store.write();
        message("slopcam.msg.deleted");
    }

    public static void message(String key, Object... args) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.gui != null) mc.gui.setOverlayMessage(Component.translatable(key, args), false);
    }
}
