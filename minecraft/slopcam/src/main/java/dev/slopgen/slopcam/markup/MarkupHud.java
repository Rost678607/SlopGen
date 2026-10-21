package dev.slopgen.slopcam.markup;

import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.ItemStack;

/** The markup hotbar and what the mode says on screen: the tool, how to use it, where you are. */
public final class MarkupHud {
    private static final ResourceLocation HOTBAR = ResourceLocation.withDefaultNamespace("hud/hotbar");
    private static final ResourceLocation SELECTION = ResourceLocation.withDefaultNamespace("hud/hotbar_selection");

    private MarkupHud() {
    }

    public static void render(GuiGraphics g, MarkupMode mode, RegistryStore store) {
        if (!mode.active()) return;
        Minecraft mc = Minecraft.getInstance();
        if (mc.options.hideGui) return;
        Font font = mc.font;
        int w = g.guiWidth(), h = g.guiHeight();
        int x = w / 2 - 91, y = h - 22;
        g.blitSprite(HOTBAR, x, y, 182, 22);
        Tool[] tools = Tool.values();
        for (int i = 0; i < tools.length; i++) {
            g.renderItem(new ItemStack(tools[i].icon), x + 3 + i * 20, y + 3);
        }
        g.blitSprite(SELECTION, x - 1 + mode.tool().ordinal() * 20, y - 1, 24, 23);

        Component name = Component.translatable(mode.tool().nameKey());
        Component hint = Component.translatable(mode.tool().hintKey());
        g.drawCenteredString(font, name, w / 2, y - 22, 0xFFFFFF);
        g.drawCenteredString(font, hint, w / 2, y - 11, 0xA0A0A0);

        Registry reg = store.get();
        Registry.Place place = mode.place();
        int line = 4;
        g.drawString(font, Component.translatable("slopcam.hud.mode"), 4, line, 0x55FFFF);
        line += 11;
        g.drawString(font, Component.translatable("slopcam.hud.place", place == null ? "—" : place.name), 4, line, 0xFFFFFF);
        line += 11;
        Registry.Element sel = reg.byId(mode.selectedId());
        if (sel != null) {
            g.drawString(font, Component.translatable("slopcam.hud.selected", sel.name), 4, line, 0xFFFF55);
            line += 11;
        }
        g.drawString(font, Component.translatable("slopcam.hud.keys"), 4, line, 0x808080);

        // the crosshair: spectators normally get none, and the markup tools aim with it
        g.fill(w / 2 - 4, h / 2, w / 2 + 5, h / 2 + 1, 0xC0FFFFFF);
        g.fill(w / 2, h / 2 - 4, w / 2 + 1, h / 2 + 5, 0xC0FFFFFF);
    }
}
