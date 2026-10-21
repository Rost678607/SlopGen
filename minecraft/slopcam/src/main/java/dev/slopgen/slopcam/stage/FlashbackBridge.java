package dev.slopgen.slopcam.stage;

import com.moulberry.flashback.Flashback;
import com.moulberry.flashback.record.Recorder;
import com.moulberry.flashback.record.ReplayExporter;
import net.fabricmc.loader.api.FabricLoader;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Every call slopcam makes into Flashback, in one class.
 *
 * <p>Flashback has no API; these are its internals, read from its source (branch
 * {@code 1.21}) and pinned to the version in {@code gradle.properties}. Keeping them
 * here is what makes a Flashback update a one-file job, and {@link #check()} is what
 * turns a mismatch into a sentence instead of a crash in the middle of a take.
 */
public final class FlashbackBridge {
    public static final String EXPECTED = "0.39.8";

    private FlashbackBridge() {
    }

    /** Null when the installed Flashback is the one slopcam was written against. */
    public static String check() {
        String v = FabricLoader.getInstance().getModContainer("flashback")
                .map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse(null);
        if (v == null) return "Flashback is not installed";
        if (!v.startsWith(EXPECTED)) return "Flashback " + v + " is installed; slopcam drives " + EXPECTED;
        return null;
    }

    public static boolean recording() {
        return Flashback.RECORDER != null;
    }

    /** Start recording, as the menu's "start recording" does. Client thread. */
    public static void start() {
        if (Flashback.RECORDER != null) throw new IllegalStateException("Flashback is already recording");
        Flashback.startRecordingReplay();
        if (Flashback.RECORDER == null) {
            throw new IllegalStateException("Flashback refused to record (an incompatible mod?)");
        }
    }

    /**
     * Stop recording and write the replay to `out`, under `name` — what the menu's
     * "finish" does, minus the dialog asking where to save. Client thread.
     */
    public static Path finish(Path out, String name) throws Exception {
        Recorder recorder = Flashback.RECORDER;
        if (recorder == null) throw new IllegalStateException("Flashback is not recording");
        Flashback.RECORDER = null;
        recorder.endTick(true);
        Path folder = recorder.finish();
        Files.createDirectories(out.getParent());
        ReplayExporter.export(folder, out, name);
        return out;
    }

    /** Throw a recording away (a take that failed half way). */
    public static void cancel() {
        Recorder recorder = Flashback.RECORDER;
        if (recorder == null) return;
        Flashback.RECORDER = null;
        try {
            Path folder = recorder.finish();
            org.apache.commons.io.FileUtils.deleteDirectory(folder.toFile());
        } catch (Exception ignored) {
        }
    }
}
