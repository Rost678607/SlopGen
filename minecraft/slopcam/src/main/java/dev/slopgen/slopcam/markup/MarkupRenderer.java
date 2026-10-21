package dev.slopgen.slopcam.markup;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderContext;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.world.phys.Vec3;
import org.joml.Matrix4f;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The markup, drawn into the world — only in markup mode, only on this screen.
 *
 * <p>Nothing here is a block or an entity, so nothing of it is ever recorded by
 * Flashback (which records packets) or seen by anybody else; and it is skipped for the
 * frame a camera screenshot is taken in.
 *
 * <p>Areas are drawn as the outline of their floor: an edge of a cell is drawn where
 * the neighbouring cell is not in the area. That outline is cached per area and
 * thrown away whenever the registry changes.
 */
public final class MarkupRenderer {
    private static final double RANGE = 96;
    private static final int MAX_CELLS = 20000;

    private final Map<String, float[]> outlines = new HashMap<>();
    private int cachedVersion = -1;

    public void render(WorldRenderContext ctx, MarkupMode mode, RegistryStore store, Shots shots) {
        if (!mode.active() || shots.capturing()) return;
        Minecraft mc = Minecraft.getInstance();
        Registry reg = store.get();
        if (store.version() != cachedVersion) {
            outlines.clear();
            cachedVersion = store.version();
        }
        PoseStack ps = ctx.matrixStack();
        Vec3 cam = ctx.camera().getPosition();
        MultiBufferSource.BufferSource buf = mc.renderBuffers().bufferSource();
        VertexConsumer lines = buf.getBuffer(RenderType.lines());
        ps.pushPose();
        ps.translate(-cam.x, -cam.y, -cam.z);
        PoseStack.Pose pose = ps.last();
        String sel = mode.selectedId();
        List<Runnable> labels = new ArrayList<>();

        for (Registry.Area a : reg.areas) {
            int[] b = a.bounds();
            if (b == null || far(cam, (b[0] + b[3]) / 2.0, b[1], (b[2] + b[5]) / 2.0, RANGE + (b[3] - b[0]))) continue;
            int color = a.roles.contains("forbidden") ? 0xFF4040 : 0x40FF60;
            float alpha = a.id.equals(sel) ? 1f : 0.7f;
            float[] edges = outlines.computeIfAbsent(a.id, k -> outline(a));
            for (int i = 0; i + 5 < edges.length; i += 6) {
                line(lines, pose, edges[i], edges[i + 1], edges[i + 2], edges[i + 3], edges[i + 4], edges[i + 5], color, alpha);
            }
            labels.add(() -> label(ps, buf, a.name, (b[0] + b[3] + 1) / 2.0, b[1] + 0.3, (b[2] + b[5] + 1) / 2.0, color, a.id.equals(sel)));
        }
        for (Registry.Point p : reg.points) {
            if (far(cam, p.pos[0], p.pos[1], p.pos[2], RANGE)) continue;
            int color = p.roles.contains("seat") ? 0x4080FF : p.roles.contains("entry") ? 0xC060FF : 0xFFD040;
            float alpha = p.id.equals(sel) ? 1f : 0.85f;
            double x = p.pos[0], y = p.pos[1], z = p.pos[2];
            line(lines, pose, x, y, z, x, y + 1.8, z, color, alpha);
            double r = Math.toRadians(p.yaw);
            double fx = -Math.sin(r), fz = Math.cos(r);
            double tx = x + fx * 0.8, tz = z + fz * 0.8;
            line(lines, pose, x, y + 0.05, z, tx, y + 0.05, tz, color, alpha);
            line(lines, pose, tx, y + 0.05, tz, tx - fx * 0.25 + fz * 0.15, y + 0.05, tz - fz * 0.25 - fx * 0.15, color, alpha);
            line(lines, pose, tx, y + 0.05, tz, tx - fx * 0.25 - fz * 0.15, y + 0.05, tz - fz * 0.25 + fx * 0.15, color, alpha);
            LevelRenderer.renderLineBox(ps, lines, x - 0.3, y, z - 0.3, x + 0.3, y + 0.05, z + 0.3, r(color), g(color), b(color), alpha);
            labels.add(() -> label(ps, buf, p.name, x, y + 2.1, z, color, p.id.equals(sel)));
        }
        for (Registry.Line l : reg.lines) {
            if (l.points.isEmpty() || far(cam, l.points.get(0)[0], l.points.get(0)[1], l.points.get(0)[2], RANGE * 2)) continue;
            int color = l.roles.contains("rail") ? 0x40E0FF : l.roles.contains("queue") ? 0xFF9030 : 0xF0F0F0;
            float alpha = l.id.equals(sel) ? 1f : 0.85f;
            for (int i = 0; i + 1 < l.points.size(); i++) {
                double[] a = l.points.get(i), c = l.points.get(i + 1);
                line(lines, pose, a[0], a[1] + 0.1, a[2], c[0], c[1] + 0.1, c[2], color, alpha);
            }
            for (double[] a : l.points) {
                LevelRenderer.renderLineBox(ps, lines, a[0] - 0.12, a[1], a[2] - 0.12, a[0] + 0.12, a[1] + 0.24, a[2] + 0.12, r(color), g(color), b(color), alpha);
            }
            double[] first = l.points.get(0);
            labels.add(() -> label(ps, buf, l.name, first[0], first[1] + 0.6, first[2], color, l.id.equals(sel)));
        }
        for (Registry.Camera c : reg.cameras) {
            if (far(cam, c.pos[0], c.pos[1], c.pos[2], RANGE)) continue;
            int color = 0xFF50C0;
            frustum(lines, pose, c, color, c.id.equals(sel) ? 1f : 0.8f);
            labels.add(() -> label(ps, buf, c.name, c.pos[0], c.pos[1] + 0.5, c.pos[2], color, c.id.equals(sel)));
        }
        for (long k : mode.ghosts()) {
            int[] d = unpack(k);  // Registry.pack, not BlockPos.asLong
            LevelRenderer.renderLineBox(ps, lines, d[0] + 0.2, d[1], d[2] + 0.2, d[0] + 0.8, d[1] + 1.8, d[2] + 0.8, 1f, 1f, 1f, 0.9f);
        }
        drafts(ps, lines, pose, mode);
        buf.endBatch(RenderType.lines());
        for (Runnable lbl : labels) lbl.run();
        buf.endBatch();
        ps.popPose();
    }

    private static int[] unpack(long k) {
        int x = (int) (k >> 38), z = (int) (k << 26 >> 38), y = (int) (k << 52 >> 52);
        return new int[]{x, y, z};
    }

    private void drafts(PoseStack ps, VertexConsumer lines, PoseStack.Pose pose, MarkupMode mode) {
        BlockPos at = Pick.feet();
        BlockPos c = mode.boxCorner();
        if (c != null && at != null) {
            LevelRenderer.renderLineBox(ps, lines,
                    Math.min(c.getX(), at.getX()), Math.min(c.getY(), at.getY()), Math.min(c.getZ(), at.getZ()),
                    Math.max(c.getX(), at.getX()) + 1, Math.max(c.getY(), at.getY()) + 1, Math.max(c.getZ(), at.getZ()) + 1,
                    1f, 1f, 1f, 1f);
        }
        List<double[]> poly = mode.polygonDraft();
        double py = mode.polygonY() + 0.05;
        for (int i = 0; i < poly.size(); i++) {
            double[] a = poly.get(i);
            double[] b = i + 1 < poly.size() ? poly.get(i + 1) : at == null ? null : new double[]{at.getX() + 0.5, at.getZ() + 0.5};
            if (b != null) line(lines, pose, a[0], py, a[1], b[0], py, b[1], 0xFFFFFF, 1f);
        }
        List<double[]> ln = mode.lineDraft();
        for (int i = 0; i < ln.size(); i++) {
            double[] a = ln.get(i);
            double[] b = i + 1 < ln.size() ? ln.get(i + 1) : at == null ? null : new double[]{at.getX() + 0.5, at.getY(), at.getZ() + 0.5};
            if (b != null) line(lines, pose, a[0], a[1] + 0.1, a[2], b[0], b[1] + 0.1, b[2], 0xFFFFFF, 1f);
        }
        if (at != null && mode.tool() != Tool.SELECT && mode.tool() != Tool.PROPERTIES && mode.tool() != Tool.CAMERA) {
            LevelRenderer.renderLineBox(ps, lines, at.getX(), at.getY(), at.getZ(), at.getX() + 1, at.getY() + 0.02, at.getZ() + 1, 1f, 1f, 1f, 0.6f);
        }
    }

    /** The floor outline of an area as a flat list of segments: x0 y0 z0 x1 y1 z1 … */
    private static float[] outline(Registry.Area a) {
        List<int[]> cells = a.cells(MAX_CELLS);
        java.util.Set<Long> in = new java.util.HashSet<>();
        for (int[] c : cells) in.add(Registry.pack(c[0], c[1], c[2]));
        List<Float> out = new ArrayList<>();
        for (int[] c : cells) {
            int x = c[0], y = c[1], z = c[2];
            float fy = y + 0.03f;
            if (!in.contains(Registry.pack(x, y, z - 1))) seg(out, x, fy, z, x + 1, fy, z);
            if (!in.contains(Registry.pack(x, y, z + 1))) seg(out, x, fy, z + 1, x + 1, fy, z + 1);
            if (!in.contains(Registry.pack(x - 1, y, z))) seg(out, x, fy, z, x, fy, z + 1);
            if (!in.contains(Registry.pack(x + 1, y, z))) seg(out, x + 1, fy, z, x + 1, fy, z + 1);
        }
        float[] arr = new float[out.size()];
        for (int i = 0; i < arr.length; i++) arr[i] = out.get(i);
        return arr;
    }

    private static void seg(List<Float> out, float x0, float y0, float z0, float x1, float y1, float z1) {
        out.add(x0);
        out.add(y0);
        out.add(z0);
        out.add(x1);
        out.add(y1);
        out.add(z1);
    }

    private static void frustum(VertexConsumer lines, PoseStack.Pose pose, Registry.Camera c, int color, float alpha) {
        // a portrait frame (9:16), a block deep, pointing where the camera looks
        double yaw = Math.toRadians(c.yaw), pitch = Math.toRadians(c.pitch);
        Vec3 fwd = new Vec3(-Math.sin(yaw) * Math.cos(pitch), -Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
        Vec3 right = new Vec3(-Math.cos(yaw), 0, -Math.sin(yaw));
        Vec3 up = right.cross(fwd).normalize();
        double h = Math.tan(Math.toRadians(c.fov) / 2), w = h * 9 / 16;
        Vec3 o = new Vec3(c.pos[0], c.pos[1], c.pos[2]);
        Vec3[] corners = new Vec3[4];
        int k = 0;
        for (int sy : new int[]{1, -1}) {
            for (int sx : new int[]{-1, 1}) {
                corners[k++] = o.add(fwd).add(right.scale(sx * w)).add(up.scale(sy * h));
            }
        }
        for (Vec3 v : corners) line(lines, pose, o.x, o.y, o.z, v.x, v.y, v.z, color, alpha);
        int[][] ring = {{0, 1}, {1, 3}, {3, 2}, {2, 0}};
        for (int[] e : ring) {
            Vec3 a = corners[e[0]], b = corners[e[1]];
            line(lines, pose, a.x, a.y, a.z, b.x, b.y, b.z, color, alpha);
        }
    }

    private static void line(VertexConsumer vc, PoseStack.Pose pose, double x0, double y0, double z0,
                             double x1, double y1, double z1, int color, float alpha) {
        float dx = (float) (x1 - x0), dy = (float) (y1 - y0), dz = (float) (z1 - z0);
        float len = (float) Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (len < 1e-6) return;
        dx /= len;
        dy /= len;
        dz /= len;
        vc.addVertex(pose, (float) x0, (float) y0, (float) z0).setColor(r(color), g(color), b(color), alpha).setNormal(pose, dx, dy, dz);
        vc.addVertex(pose, (float) x1, (float) y1, (float) z1).setColor(r(color), g(color), b(color), alpha).setNormal(pose, dx, dy, dz);
    }

    private static void label(PoseStack ps, MultiBufferSource buf, String text, double x, double y, double z, int color, boolean selected) {
        Minecraft mc = Minecraft.getInstance();
        Font font = mc.font;
        ps.pushPose();
        ps.translate(x, y, z);
        ps.mulPose(mc.gameRenderer.getMainCamera().rotation());
        float s = selected ? 0.03f : 0.022f;
        ps.scale(s, -s, s);
        Matrix4f m = ps.last().pose();
        Component c = Component.literal(text);
        float w = -font.width(c) / 2f;
        font.drawInBatch(c, w, 0, 0xFF000000 | color, false, m, buf, Font.DisplayMode.SEE_THROUGH, 0x60000000, 0xF000F0);
        ps.popPose();
    }

    private static boolean far(Vec3 cam, double x, double y, double z, double range) {
        double dx = cam.x - x, dy = cam.y - y, dz = cam.z - z;
        return dx * dx + dy * dy + dz * dz > range * range;
    }

    private static float r(int c) {
        return ((c >> 16) & 0xFF) / 255f;
    }

    private static float g(int c) {
        return ((c >> 8) & 0xFF) / 255f;
    }

    private static float b(int c) {
        return (c & 0xFF) / 255f;
    }
}
