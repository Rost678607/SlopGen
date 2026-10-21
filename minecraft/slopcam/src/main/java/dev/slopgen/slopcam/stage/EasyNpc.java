package dev.slopgen.slopcam.stage;

import de.markusbordihn.easynpc.data.model.ModelPartType;
import de.markusbordihn.easynpc.data.model.ModelPose;
import de.markusbordihn.easynpc.data.position.CustomPosition;
import de.markusbordihn.easynpc.data.rotation.CustomRotation;
import de.markusbordihn.easynpc.entity.easynpc.data.ModelDataCapable;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.world.entity.Entity;

/**
 * The few things slopcam asks of Easy NPC, in one place: put the model in custom-pose
 * mode, and set one part's rotation or offset.
 *
 * <p>Easy NPC plays keyframe animations only on another mod's models, so gestures are
 * played here a tick at a time through the custom pose. The rotation is synced
 * entity data, which is also what lets Flashback record it. Units are a config
 * switch ({@code poseRadians}, {@code positionScale}) until they are confirmed in game.
 */
public final class EasyNpc {
    private EasyNpc() {
    }

    public static boolean is(Entity e) {
        try {
            return e instanceof ModelDataCapable<?>;
        } catch (NoClassDefFoundError err) {
            return false;
        }
    }

    public static void customPose(Entity e, boolean custom) {
        if (!(e instanceof ModelDataCapable<?> m)) return;
        m.setModelPose(custom ? ModelPose.CUSTOM : ModelPose.VANILLA);
    }

    public static void rotate(Entity e, String part, float xDeg, float yDeg, float zDeg) {
        if (!(e instanceof ModelDataCapable<?> m)) return;
        ModelPartType type = part(part);
        if (type == null) return;
        float k = SlopcamClient.config().poseRadians ? (float) (Math.PI / 180.0) : 1f;
        m.setModelPartRotation(type, new CustomRotation(xDeg * k, yDeg * k, zDeg * k));
    }

    public static void offset(Entity e, String part, float x, float y, float z) {
        if (!(e instanceof ModelDataCapable<?> m)) return;
        ModelPartType type = part(part);
        if (type == null) return;
        float k = SlopcamClient.config().positionScale;
        m.setModelPartPosition(type, new CustomPosition(x * k, y * k, z * k));
    }

    private static ModelPartType part(String name) {
        try {
            return ModelPartType.valueOf(name);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
