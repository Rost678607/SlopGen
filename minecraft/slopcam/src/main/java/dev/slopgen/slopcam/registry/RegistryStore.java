package dev.slopgen.slopcam.registry;

import dev.slopgen.slopcam.Json;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.Minecraft;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.world.level.storage.LevelResource;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * The registry of the world currently open, and the file it lives in:
 * {@code <save>/slopgen/registry.json}.
 *
 * <p>The file is the single source of truth, because two programs edit it. Every
 * change made here is written at once (atomically — slopgen may be reading it), and a
 * change made there is noticed by its timestamp and loaded. Edits are also kept on an
 * undo stack: markup is drawn with the mouse, and a misplaced corner should cost a
 * keypress, not a redraw.
 */
public final class RegistryStore {
    private static final int UNDO_DEPTH = 64;

    private Registry registry = new Registry();
    private Path saveDir;
    private long seenModified;
    private final Deque<String> undo = new ArrayDeque<>();
    private int version;

    public Registry get() {
        return registry;
    }

    /** Goes up on every change, so whatever is cached off the registry knows to redo it. */
    public int version() {
        return version;
    }

    /** A change made in memory that is not written yet (a brush stroke in progress). */
    public void bump() {
        version++;
    }

    public Path saveDir() {
        return saveDir;
    }

    public Path dir() {
        return saveDir == null ? null : saveDir.resolve("slopgen");
    }

    public Path file() {
        return saveDir == null ? null : dir().resolve("registry.json");
    }

    /** Follow the world: open the registry of the singleplayer world joined, drop it on leaving. */
    public void tick() {
        IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
        Path now = server == null ? null : server.getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize();
        if (now == null ? saveDir != null : !now.equals(saveDir)) {
            saveDir = now;
            undo.clear();
            load();
            SlopcamClient.events().push("world", saveDir == null ? null : saveDir.toString());
            return;
        }
        if (saveDir != null && Files.isRegularFile(file())) {
            try {
                long m = Files.getLastModifiedTime(file()).toMillis();
                if (m != seenModified) {
                    load();
                    SlopcamClient.events().push("registry.changed", "outside");
                }
            } catch (IOException ignored) {
            }
        }
    }

    public void load() {
        version++;
        registry = new Registry();
        seenModified = 0;
        if (saveDir == null || !Files.isRegularFile(file())) return;
        try {
            String text = Files.readString(file(), StandardCharsets.UTF_8);
            Registry r = Json.GSON.fromJson(text, Registry.class);
            if (r != null) registry = r;
            seenModified = Files.getLastModifiedTime(file()).toMillis();
        } catch (Exception e) {
            SlopcamClient.LOG.error("slopcam: cannot read {}", file(), e);
        }
    }

    /** Remember the state before an edit, so it can be undone. Call BEFORE changing anything. */
    public void checkpoint() {
        undo.push(Json.GSON.toJson(registry));
        while (undo.size() > UNDO_DEPTH) undo.removeLast();
    }

    public boolean undo() {
        if (undo.isEmpty()) return false;
        registry = Json.GSON.fromJson(undo.pop(), Registry.class);
        write();
        return true;
    }

    /** Replace the whole registry (the browser's save, through the socket). */
    public void replace(Registry r) {
        checkpoint();
        registry = r;
        write();
    }

    public void write() {
        version++;
        if (saveDir == null) return;
        try {
            Files.createDirectories(dir());
            Path tmp = Files.createTempFile(dir(), ".registry.", ".json");
            Files.writeString(tmp, Json.PRETTY.toJson(registry), StandardCharsets.UTF_8);
            Files.move(tmp, file(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            seenModified = Files.getLastModifiedTime(file()).toMillis();
            SlopcamClient.events().push("registry.changed", "game");
        } catch (IOException e) {
            SlopcamClient.LOG.error("slopcam: cannot write {}", file(), e);
        }
    }
}
