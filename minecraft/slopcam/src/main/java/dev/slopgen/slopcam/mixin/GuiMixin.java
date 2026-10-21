package dev.slopgen.slopcam.mixin;

import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.gui.Gui;
import net.minecraft.client.gui.GuiGraphics;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** In the markup mode the hotbar is the markup tools; the spectator's own hotbar stays hidden. */
@Mixin(Gui.class)
abstract class GuiMixin {
    @Inject(method = "renderHotbarAndDecorations", at = @At("HEAD"), cancellable = true)
    private void slopcam$hideHotbar(GuiGraphics g, DeltaTracker delta, CallbackInfo ci) {
        if (SlopcamClient.markup() != null && SlopcamClient.markup().active()) ci.cancel();
    }
}
