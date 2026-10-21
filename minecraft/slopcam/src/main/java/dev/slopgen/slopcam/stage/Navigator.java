package dev.slopgen.slopcam.stage;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;

/**
 * Finding the way for an actor, ourselves.
 *
 * <p>Easy NPC can pathfind, but only with its AI on, and its AI brings the NPC's own
 * objectives along — a copied NPC would wander off on its own errands mid-scene. So
 * actors are puppets with their AI off, and their way is an A* over standable cells
 * here, then pulled straight wherever the line between two corners is clear, so they
 * walk like people and not like rooks.
 */
public final class Navigator {
    private static final int MAX_NODES = 20000;
    private static final int[][] DIRS = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}, {1, 1}, {1, -1}, {-1, 1}, {-1, -1}};

    private final ServerLevel level;

    public Navigator(ServerLevel level) {
        this.level = level;
    }

    private boolean passable(BlockPos p) {
        return level.getBlockState(p).getCollisionShape(level, p).isEmpty();
    }

    /** Feet and head free, something to stand on. */
    public boolean standable(BlockPos p) {
        return passable(p) && passable(p.above()) && !passable(p.below());
    }

    /** The nearest standable cell to a point, looking a little up and down. */
    public BlockPos snap(Vec3 v) {
        BlockPos base = BlockPos.containing(v);
        for (int r = 0; r <= 3; r++) {
            for (int dy : new int[]{0, -1, 1, -2, 2}) {
                for (int dx = -r; dx <= r; dx++) {
                    for (int dz = -r; dz <= r; dz++) {
                        if (Math.max(Math.abs(dx), Math.abs(dz)) != r) continue;
                        BlockPos p = base.offset(dx, dy, dz);
                        if (standable(p)) return p;
                    }
                }
            }
        }
        return base;
    }

    /** A walkable polyline of feet positions from `from` to `to`, or a straight line when there is none. */
    public List<Vec3> path(Vec3 from, Vec3 to) {
        BlockPos start = snap(from), goal = snap(to);
        Map<BlockPos, BlockPos> came = new HashMap<>();
        Map<BlockPos, Double> cost = new HashMap<>();
        PriorityQueue<Object[]> open = new PriorityQueue<>((a, b) -> Double.compare((double) a[0], (double) b[0]));
        cost.put(start, 0.0);
        open.add(new Object[]{h(start, goal), start});
        int seen = 0;
        BlockPos found = null;
        while (!open.isEmpty() && seen++ < MAX_NODES) {
            BlockPos cur = (BlockPos) open.poll()[1];
            if (cur.equals(goal)) {
                found = cur;
                break;
            }
            for (int[] d : DIRS) {
                for (int dy : new int[]{0, 1, -1}) {
                    BlockPos n = cur.offset(d[0], dy, d[1]);
                    if (!standable(n)) continue;
                    if (dy == 1 && !passable(cur.above(2))) continue; // room to step up
                    if (d[0] != 0 && d[1] != 0 && (!passable(cur.offset(d[0], 0, 0)) || !passable(cur.offset(0, 0, d[1])))) {
                        continue; // no cutting a corner through a wall
                    }
                    double c = cost.get(cur) + Math.sqrt(d[0] * d[0] + d[1] * d[1] + dy * dy);
                    if (c < cost.getOrDefault(n, Double.MAX_VALUE)) {
                        cost.put(n, c);
                        came.put(n, cur);
                        open.add(new Object[]{c + h(n, goal), n});
                    }
                }
            }
        }
        List<Vec3> out = new ArrayList<>();
        if (found == null) {
            out.add(from);
            out.add(to);
            return out;
        }
        List<BlockPos> cells = new ArrayList<>();
        for (BlockPos p = found; p != null; p = came.get(p)) cells.add(p);
        Collections.reverse(cells);
        for (BlockPos p : smooth(cells)) out.add(Vec3.atBottomCenterOf(p));
        out.set(out.size() - 1, to);
        return out;
    }

    private List<BlockPos> smooth(List<BlockPos> cells) {
        if (cells.size() < 3) return cells;
        List<BlockPos> out = new ArrayList<>();
        int i = 0;
        out.add(cells.get(0));
        while (i < cells.size() - 1) {
            int j = cells.size() - 1;
            while (j > i + 1 && !straight(cells.get(i), cells.get(j))) j--;
            out.add(cells.get(j));
            i = j;
        }
        return out;
    }

    /** Can somebody walk straight from a to b on the same floor? */
    private boolean straight(BlockPos a, BlockPos b) {
        if (a.getY() != b.getY()) return false;
        Vec3 va = Vec3.atBottomCenterOf(a), vb = Vec3.atBottomCenterOf(b);
        double len = va.distanceTo(vb);
        int steps = (int) Math.ceil(len * 3);
        for (int k = 1; k < steps; k++) {
            Vec3 p = va.lerp(vb, k / (double) steps);
            // the body is about 0.6 wide: check both shoulders, not just the spine
            Vec3 side = new Vec3(vb.z - va.z, 0, va.x - vb.x).normalize().scale(0.3);
            for (Vec3 q : new Vec3[]{p, p.add(side), p.subtract(side)}) {
                if (!standable(BlockPos.containing(q.x, va.y, q.z))) return false;
            }
        }
        return true;
    }

    private static double h(BlockPos a, BlockPos b) {
        return Math.sqrt(a.distSqr(b));
    }
}
