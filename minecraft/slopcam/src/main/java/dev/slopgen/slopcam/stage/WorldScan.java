package dev.slopgen.slopcam.stage;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.state.BlockState;

import java.util.Base64;

/**
 * The blocks around a take, as slopgen's camera search needs them
 * ({@code slopgen.minecraft.voxels.World}): one bit per block for SOLID (bodies and the
 * camera cannot be there) and one for OPAQUE (it hides what is behind it). Glass is
 * the block that tells them apart.
 *
 * <p>Layout: index {@code (y*sz + z)*sx + x}, bits little-endian within a byte.
 */
public final class WorldScan {
    private WorldScan() {
    }

    public static JsonObject scan(ServerLevel level, BlockPos min, BlockPos max) {
        int sx = max.getX() - min.getX() + 1, sy = max.getY() - min.getY() + 1, sz = max.getZ() - min.getZ() + 1;
        int n = sx * sy * sz;
        byte[] solid = new byte[(n + 7) / 8], opaque = new byte[(n + 7) / 8];
        BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
        for (int y = 0; y < sy; y++) {
            for (int z = 0; z < sz; z++) {
                for (int x = 0; x < sx; x++) {
                    p.set(min.getX() + x, min.getY() + y, min.getZ() + z);
                    BlockState s = level.getBlockState(p);
                    if (s.isAir()) continue;
                    int i = (y * sz + z) * sx + x;
                    if (!s.getCollisionShape(level, p).isEmpty()) solid[i >> 3] |= (byte) (1 << (i & 7));
                    if (s.canOcclude() || s.isSolidRender(level, p)) opaque[i >> 3] |= (byte) (1 << (i & 7));
                }
            }
        }
        JsonObject o = new JsonObject();
        com.google.gson.JsonArray origin = new com.google.gson.JsonArray(), size = new com.google.gson.JsonArray();
        origin.add(min.getX());
        origin.add(min.getY());
        origin.add(min.getZ());
        size.add(sx);
        size.add(sy);
        size.add(sz);
        o.add("origin", origin);
        o.add("size", size);
        o.addProperty("solid", Base64.getEncoder().encodeToString(solid));
        o.addProperty("opaque", Base64.getEncoder().encodeToString(opaque));
        return o;
    }
}
