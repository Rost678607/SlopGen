package dev.slopgen.slopcam.mixin;

import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** The wheel picks a markup tool, as it picks a hotbar slot — not the spectator's flying speed. */
@Mixin(MouseHandler.class)
abstract class MouseHandlerMixin {
    @Inject(method = "onScroll", at = @At("HEAD"), cancellable = true)
    private void slopcam$scroll(long window, double dx, double dy, CallbackInfo ci) {
        Minecraft mc = Minecraft.getInstance();
        if (window != mc.getWindow().getWindow() || mc.screen != null) return;
        if (SlopcamClient.markup() == null || !SlopcamClient.markup().active()) return;
        if (dy != 0) SlopcamClient.markup().scroll(dy);
        ci.cancel();
    }
}
