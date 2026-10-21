package dev.slopgen.slopcam.stage;

import com.google.gson.JsonObject;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.Pose;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/**
 * One actor during a take: an Easy NPC copy with its AI off, moved by us.
 *
 * <p>Everything an actor does is set here, on the server, once a tick: where it is
 * (walking a path at a steady pace — the client turns the steps into a walk), which
 * way the body faces (along the walk, or turned to a target), where the head looks,
 * and every body part's rotation from the dense tracks slopgen composed. The client
 * draws it and Flashback records what the client was sent.
 */
public final class Puppet {
    public final String id;
    public final LivingEntity entity;
    private final Script.Body body;
    private final List<Vec3> path = new ArrayList<>();
    private double speed;
    private JsonObject then;
    private Script.Target look;
    private int lookUntil = Integer.MAX_VALUE;
    private Float turnTo;
    private Script.Target turnTarget;
    private String lastVanilla = "STANDING";

    public Puppet(String id, LivingEntity entity, Script.Body body) {
        this.id = id;
        this.entity = entity;
        this.body = body;
        if (entity instanceof Mob m) m.setNoAi(true);
        entity.setInvulnerable(true);
        entity.setSilent(true);
        entity.addTag("slopgen");
        EasyNpc.customPose(entity, true);
    }

    public boolean walking() {
        return !path.isEmpty();
    }

    public void walk(List<Vec3> route, double speedMultiplier, JsonObject then) {
        path.clear();
        path.addAll(route);
        if (!path.isEmpty() && path.get(0).distanceTo(entity.position()) < 0.3) path.remove(0);
        this.speed = SlopcamClient.config().walkSpeed * speedMultiplier / 20.0;
        this.then = then;
    }

    public void look(Script.Target t, Integer until) {
        look = t;
        lookUntil = until == null ? Integer.MAX_VALUE : until;
    }

    public void turn(Script.Target t) {
        turnTarget = t;
    }

    public void face(float yaw) {
        turnTo = yaw;
    }

    /** One tick of the take. `where` resolves a target to the position of its eyes. */
    public JsonObject tick(int t, Function<Script.Target, Vec3> where) {
        JsonObject finished = null;
        Vec3 pos = entity.position();
        if (!path.isEmpty()) {
            double left = speed;
            while (left > 0 && !path.isEmpty()) {
                Vec3 next = path.get(0);
                Vec3 d = next.subtract(pos);
                double dist = d.length();
                if (dist <= left) {
                    pos = next;
                    left -= dist;
                    path.remove(0);
                } else {
                    pos = pos.add(d.scale(left / dist));
                    left = 0;
                }
            }
            Vec3 move = pos.subtract(entity.position());
            if (move.horizontalDistanceSqr() > 1e-6) {
                turnTo = (float) (Mth.atan2(move.z, move.x) * (180 / Math.PI)) - 90f;
            }
            entity.setPos(pos);
            if (path.isEmpty() && then != null) {
                finished = then;
                then = null;
            }
        }
        if (turnTarget != null) {
            Vec3 at = where.apply(turnTarget);
            if (at != null) {
                Vec3 d = at.subtract(entity.position());
                if (d.horizontalDistanceSqr() > 1e-4) turnTo = (float) (Mth.atan2(d.z, d.x) * (180 / Math.PI)) - 90f;
            }
            if (!walking()) turnTarget = null;
        }
        if (turnTo != null) {
            // turn at a human pace rather than snapping: about 400°/s
            float cur = entity.getYRot();
            float step = Mth.clamp(Mth.wrapDegrees(turnTo - cur), -20f, 20f);
            float yaw = cur + step;
            entity.setYRot(yaw);
            entity.setYBodyRot(yaw);
            if (Math.abs(Mth.wrapDegrees(turnTo - yaw)) < 0.5f) turnTo = null;
        }
        float headYaw = entity.getYRot(), headPitch = 0f;
        if (look != null && t <= lookUntil) {
            Vec3 at = where.apply(look);
            if (at != null) {
                Vec3 eye = entity.getEyePosition();
                Vec3 d = at.subtract(eye);
                headYaw = (float) (Mth.atan2(d.z, d.x) * (180 / Math.PI)) - 90f;
                headPitch = (float) -(Mth.atan2(d.y, Math.sqrt(d.x * d.x + d.z * d.z)) * (180 / Math.PI));
                // a head turns only so far before the body has to follow
                float rel = Mth.wrapDegrees(headYaw - entity.getYRot());
                if (Math.abs(rel) > 70f && !walking()) turnTo = headYaw;
                headYaw = entity.getYRot() + Mth.clamp(rel, -70f, 70f);
            }
        } else if (look != null) {
            look = null;
        }
        entity.setYHeadRot(headYaw);
        entity.setXRot(headPitch);
        pose(t);
        return finished;
    }

    private void pose(int t) {
        if (body == null) return;
        for (Map.Entry<String, List<Object[]>> e : body.parts.entrySet()) {
            float[] r = sample(e.getValue(), t);
            if (r != null) EasyNpc.rotate(entity, e.getKey(), r[0], r[1], r[2]);
        }
        float[] off = sample(body.offset, t);
        if (off != null) EasyNpc.offset(entity, "ROOT", off[0], off[1], off[2]);
        String vanilla = step(body.vanilla, t);
        if (vanilla != null && !vanilla.equals(lastVanilla)) {
            lastVanilla = vanilla;
            boolean standing = vanilla.equals("STANDING");
            EasyNpc.customPose(entity, standing);
            try {
                entity.setPose(Pose.valueOf(vanilla));
            } catch (IllegalArgumentException ignored) {
            }
        }
    }

    /** Linear between the keys around `t`; the keys are dense enough that this is the curve. */
    static float[] sample(List<Object[]> keys, int t) {
        if (keys == null || keys.isEmpty()) return null;
        Object[] prev = null;
        for (Object[] k : keys) {
            int kt = ((Number) k[0]).intValue();
            if (kt >= t) {
                float[] b = vec(k[1]);
                if (prev == null || kt == t) return b;
                int pt = ((Number) prev[0]).intValue();
                float[] a = vec(prev[1]);
                float f = (t - pt) / (float) (kt - pt);
                return new float[]{a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f};
            }
            prev = k;
        }
        return vec(prev[1]);
    }

    private static String step(List<Object[]> keys, int t) {
        String out = null;
        for (Object[] k : keys) {
            if (((Number) k[0]).intValue() <= t) out = String.valueOf(k[1]);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static float[] vec(Object o) {
        List<Number> l = (List<Number>) o;
        return new float[]{l.get(0).floatValue(), l.get(1).floatValue(), l.get(2).floatValue()};
    }

    public Vec3 eyes() {
        return entity.getEyePosition();
    }

    public void remove() {
        entity.discard();
    }
}
