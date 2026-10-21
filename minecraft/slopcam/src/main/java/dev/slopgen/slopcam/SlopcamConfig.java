package dev.slopgen.slopcam;

import net.fabricmc.loader.api.FabricLoader;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** {@code config/slopcam.json}: what the operator may want different from the defaults. */
public final class SlopcamConfig {
    /** The control socket slopgen connects to. Not 8770: that one is the operator's. */
    public int port = 25599;
    /** Glass and the like do not hide what is behind them from the camera. */
    public boolean seeThroughGlass = true;
    /** Brush radius in blocks; 0 paints one cell. */
    public int brushRadius = 0;
    /** How tall a polygon piece is, from its floor, in blocks. */
    public int polygonHeight = 3;

    private static Path path() {
        return FabricLoader.getInstance().getConfigDir().resolve("slopcam.json");
    }

    public static SlopcamConfig load() {
        try {
            if (Files.isRegularFile(path())) {
                SlopcamConfig c = Json.GSON.fromJson(Files.readString(path(), StandardCharsets.UTF_8), SlopcamConfig.class);
                if (c != null) return c;
            }
        } catch (Exception e) {
            SlopcamClient.LOG.warn("slopcam: bad config, using defaults", e);
        }
        SlopcamConfig c = new SlopcamConfig();
        c.save();
        return c;
    }

    public void save() {
        try {
            Files.writeString(path(), Json.PRETTY.toJson(this), StandardCharsets.UTF_8);
        } catch (Exception e) {
            SlopcamClient.LOG.warn("slopcam: cannot save config", e);
        }
    }
}
