package dev.slopgen.slopcam.markup;

import dev.slopgen.slopcam.registry.Registry;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.projectile.ProjectileUtil;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/** What the crosshair is on: a block face, a feet cell, a piece of markup, an NPC. */
public final class Pick {
    public static final double REACH = 64.0;

    private Pick() {
    }

    public static Vec3 eye() {
        return Minecraft.getInstance().gameRenderer.getMainCamera().getPosition();
    }

    public static Vec3 look() {
        return Minecraft.getInstance().player.getViewVector(1f);
    }

    public static BlockHitResult block() {
        Minecraft mc = Minecraft.getInstance();
        HitResult h = mc.player.pick(REACH, 1f, false);
        return h instanceof BlockHitResult b && h.getType() == HitResult.Type.BLOCK ? b : null;
    }

    /**
     * The cell somebody would stand in where the crosshair points: above the block if
     * it was its top face, in front of it otherwise — so pointing at a wall puts the
     * mark against the wall, not inside it.
     */
    public static BlockPos feet() {
        BlockHitResult b = block();
        if (b == null) return null;
        return b.getDirection() == Direction.UP ? b.getBlockPos().above() : b.getBlockPos().relative(b.getDirection());
    }

    public static LivingEntity living() {
        Minecraft mc = Minecraft.getInstance();
        Vec3 from = eye(), dir = look(), to = from.add(dir.scale(REACH));
        BlockHitResult wall = block();
        double max = wall == null ? REACH * REACH : wall.getLocation().distanceToSqr(from);
        AABB box = mc.player.getBoundingBox().expandTowards(dir.scale(REACH)).inflate(1.0);
        EntityHitResult hit = ProjectileUtil.getEntityHitResult(mc.player, from, to, box,
                e -> e instanceof LivingEntity && e != mc.player && !e.isSpectator(), max);
        Entity e = hit == null ? null : hit.getEntity();
        return e instanceof LivingEntity l ? l : null;
    }

    /** The markup element nearest along the crosshair ray, or null. */
    public static Registry.Element element(Registry reg) {
        Vec3 from = eye(), dir = look();
        Registry.Element best = null;
        double bestT = Double.MAX_VALUE;
        for (Registry.Point p : reg.points) {
            Vec3 c = new Vec3(p.pos[0], p.pos[1] + 0.9, p.pos[2]);
            double t = closeAlong(from, dir, c, 0.6);
            if (t < bestT) {
                bestT = t;
                best = p;
            }
        }
        for (Registry.Camera c : reg.cameras) {
            double t = closeAlong(from, dir, new Vec3(c.pos[0], c.pos[1], c.pos[2]), 0.5);
            if (t < bestT) {
                bestT = t;
                best = c;
            }
        }
        for (Registry.Line l : reg.lines) {
            for (int i = 0; i + 1 < l.points.size(); i++) {
                double[] a = l.points.get(i), b = l.points.get(i + 1);
                double t = segmentAlong(from, dir, new Vec3(a[0], a[1] + 0.05, a[2]), new Vec3(b[0], b[1] + 0.05, b[2]), 0.4);
                if (t < bestT) {
                    bestT = t;
                    best = l;
                }
            }
        }
        if (best != null) return best;
        BlockPos f = feet();
        if (f == null) return null;
        for (Registry.Area a : reg.areas) {
            if (a.contains(f.getX(), f.getY(), f.getZ())) return a;
        }
        return null;
    }

    /** Distance along the ray to where it passes within `radius` of `c`, or MAX. */
    private static double closeAlong(Vec3 o, Vec3 d, Vec3 c, double radius) {
        double t = c.subtract(o).dot(d);
        if (t < 0 || t > REACH) return Double.MAX_VALUE;
        return o.add(d.scale(t)).distanceTo(c) <= radius ? t : Double.MAX_VALUE;
    }

    private static double segmentAlong(Vec3 o, Vec3 d, Vec3 a, Vec3 b, double radius) {
        // closest points between the ray o + t·d and the segment a + s·(b - a)
        Vec3 u = b.subtract(a), w = o.subtract(a);
        double aa = d.dot(d), bb = d.dot(u), cc = u.dot(u), dd = d.dot(w), ee = u.dot(w);
        double den = aa * cc - bb * bb;
        double s = den < 1e-9 ? 0 : Math.max(0, Math.min(1, (aa * ee - bb * dd) / den));
        double t = (bb * s - dd) / aa;
        if (t < 0 || t > REACH) return Double.MAX_VALUE;
        Vec3 p = o.add(d.scale(t)), q = a.add(u.scale(s));
        return p.distanceTo(q) <= radius ? t : Double.MAX_VALUE;
    }
}
