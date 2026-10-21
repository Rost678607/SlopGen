package dev.slopgen.slopcam.markup;

import com.mojang.blaze3d.platform.NativeImage;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.Util;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.Consumer;

/**
 * "How it looks from here": a frame of the world written to a file, for a camera
 * mark or a place. Taken at the end of the world pass, before the HUD is drawn and
 * with the markup overlay skipped for that frame, so the picture is the world and
 * nothing of slopcam is in it.
 */
public final class Shots {
    private Path pending;
    private Consumer<Path> after;

    public void request(Path file, Consumer<Path> done) {
        pending = file;
        after = done;
    }

    /** True for the frame a shot is about to be taken in: the overlay stays out of it. */
    public boolean capturing() {
        return pending != null;
    }

    /** Called at the end of the world render. */
    public void endOfWorld() {
        if (pending == null) return;
        Path file = pending;
        Consumer<Path> done = after;
        pending = null;
        after = null;
        NativeImage img = Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget());
        Util.ioPool().execute(() -> {
            try (img) {
                Files.createDirectories(file.getParent());
                img.writeToFile(file);
                if (done != null) Minecraft.getInstance().execute(() -> done.accept(file));
            } catch (Exception e) {
                SlopcamClient.LOG.warn("slopcam: cannot save {}", file, e);
            }
        });
    }
}
