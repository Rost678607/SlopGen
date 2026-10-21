package dev.slopgen.slopcam.ui;

import dev.slopgen.slopcam.SlopcamClient;
import dev.slopgen.slopcam.SlopcamConfig;
import dev.slopgen.slopcam.markup.MarkupMode;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.MultiLineEditBox;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.ConfirmScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * What E opens in the markup mode, instead of the inventory: the register of what is
 * marked up, the characters and their faces, the props, and the settings.
 */
public final class MarkupScreen extends Screen {
    private enum Tab { PLACES, ELEMENTS, CHARACTERS, PROPS, SETTINGS }

    private static Tab tab = Tab.PLACES;
    private static String propQuery = "";

    private final MarkupMode mode;
    private final RegistryStore store;
    private int page;
    private EditBox placeName;
    private MultiLineEditBox placeDescription;
    private EditBox search;
    private EditBox propName;
    private Item pickedItem;
    private final List<ItemSlot> itemSlots = new ArrayList<>();

    private record ItemSlot(int x, int y, Item item) {
    }

    public MarkupScreen(MarkupMode mode, RegistryStore store) {
        super(Component.translatable("slopcam.menu.title"));
        this.mode = mode;
        this.store = store;
    }

    @Override
    protected void init() {
        itemSlots.clear();
        int tx = width / 2 - 5 * 62 / 2;
        for (Tab t : Tab.values()) {
            Button b = Button.builder(Component.translatable("slopcam.menu.tab." + t.name().toLowerCase(Locale.ROOT)), x -> {
                tab = t;
                page = 0;
                rebuildWidgets();
            }).bounds(tx, 8, 60, 20).build();
            b.active = t != tab;
            addRenderableWidget(b);
            tx += 62;
        }
        switch (tab) {
            case PLACES -> places();
            case ELEMENTS -> elements();
            case CHARACTERS -> characters();
            case PROPS -> props();
            case SETTINGS -> settings();
        }
    }

    private int rows() {
        return Math.max(4, (height - 80) / 22);
    }

    /** Page buttons under a list of `total` rows. */
    private void pager(int x, int y, int total) {
        int pages = Math.max(1, (total + rows() - 1) / rows());
        if (pages <= 1) return;
        addRenderableWidget(Button.builder(Component.literal("<"), b -> {
            page = Math.max(0, page - 1);
            rebuildWidgets();
        }).bounds(x, y, 20, 20).build());
        addRenderableWidget(Button.builder(Component.literal((page + 1) + "/" + pages), b -> {
        }).bounds(x + 22, y, 50, 20).build()).active = false;
        addRenderableWidget(Button.builder(Component.literal(">"), b -> {
            page = Math.min(pages - 1, page + 1);
            rebuildWidgets();
        }).bounds(x + 74, y, 20, 20).build());
    }

    // -- places ---------------------------------------------------------------

    private void places() {
        Registry reg = store.get();
        int lx = width / 2 - 250, y = 40;
        List<Registry.Place> list = reg.places;
        int from = page * rows();
        for (int i = from; i < Math.min(list.size(), from + rows()); i++) {
            Registry.Place p = list.get(i);
            Button b = Button.builder(Component.literal(p.name), x -> {
                mode.setPlace(p.id);
                rebuildWidgets();
            }).bounds(lx, y, 180, 20).build();
            b.active = !p.id.equals(mode.placeId());
            addRenderableWidget(b);
            y += 22;
        }
        addRenderableWidget(Button.builder(Component.translatable("slopcam.menu.new_place"), x -> {
            store.checkpoint();
            Registry.Place p = new Registry.Place();
            p.name = Component.translatable("slopcam.default.place").getString() + " " + (reg.places.size() + 1);
            reg.places.add(p);
            store.write();
            mode.setPlace(p.id);
            rebuildWidgets();
        }).bounds(lx, y + 4, 180, 20).build());
        pager(lx, height - 26, list.size());

        Registry.Place cur = mode.place();
        if (cur == null) return;
        int rx = width / 2 - 50, ry = 40, rw = 300;
        placeName = new EditBox(font, rx, ry, rw, 20, Component.translatable("slopcam.props.name"));
        placeName.setMaxLength(64);
        placeName.setValue(cur.name);
        addRenderableWidget(placeName);
        ry += 26;
        placeDescription = new MultiLineEditBox(font, rx, ry, rw, 80,
                Component.translatable("slopcam.menu.place_hint"), Component.translatable("slopcam.props.description"));
        placeDescription.setValue(cur.description);
        addRenderableWidget(placeDescription);
        ry += 88;
        addRenderableWidget(Button.builder(Component.translatable("slopcam.button.save"), x -> {
            store.checkpoint();
            if (!placeName.getValue().isBlank()) cur.name = placeName.getValue().trim();
            cur.description = placeDescription.getValue().trim();
            store.write();
            rebuildWidgets();
        }).bounds(rx, ry, 96, 20).build());
        addRenderableWidget(Button.builder(Component.translatable("slopcam.menu.place_shot"), x -> {
            cur.screenshot = "shots/place_" + cur.id + ".png";
            store.write();
            onClose();
            SlopcamClient.shots().request(store.dir().resolve(cur.screenshot), null);
        }).tooltip(Tooltip.create(Component.translatable("slopcam.menu.place_shot_hint"))).bounds(rx + 102, ry, 96, 20).build());
        addRenderableWidget(Button.builder(Component.translatable("slopcam.button.delete"), x ->
                minecraft.setScreen(new ConfirmScreen(yes -> {
                    if (yes) {
                        store.checkpoint();
                        Registry r = store.get();
                        for (Registry.Element e : r.elements(cur.id)) r.remove(e);
                        r.places.remove(cur);
                        store.write();
                        mode.setPlace(r.places.isEmpty() ? "" : r.places.get(0).id);
                    }
                    minecraft.setScreen(this);
                }, Component.translatable("slopcam.menu.delete_place", cur.name),
                        Component.translatable("slopcam.menu.delete_place_detail", store.get().elements(cur.id).size())))
        ).bounds(rx + 204, ry, 96, 20).build());
    }

    // -- elements of the current place -----------------------------------------

    private void elements() {
        Registry.Place cur = mode.place();
        if (cur == null) return;
        List<Registry.Element> list = store.get().elements(cur.id);
        int x = width / 2 - 160, y = 40;
        int from = page * rows();
        for (int i = from; i < Math.min(list.size(), from + rows()); i++) {
            Registry.Element e = list.get(i);
            String label = Component.translatable("slopcam.kind." + e.kind()).getString() + ": " + e.name
                    + (e.roles.isEmpty() ? "" : "  [" + String.join(", ", e.roles.stream()
                    .map(r -> Component.translatable("slopcam.role." + r).getString()).toList()) + "]");
            Button b = Button.builder(Component.literal(label), z -> {
                mode.select(e.id);
                minecraft.setScreen(new PropertiesScreen(mode, store, e));
            }).bounds(x, y, 290, 20).build();
            if (!e.description.isBlank()) b.setTooltip(Tooltip.create(Component.literal(e.description)));
            addRenderableWidget(b);
            addRenderableWidget(Button.builder(Component.literal("→"), z -> {
                teleport(e);
                onClose();
            }).tooltip(Tooltip.create(Component.translatable("slopcam.menu.fly_to"))).bounds(x + 294, y, 26, 20).build());
            y += 22;
        }
        pager(x, height - 26, list.size());
    }

    private void teleport(Registry.Element e) {
        double[] at = e instanceof Registry.Point p ? p.pos
                : e instanceof Registry.Camera c ? c.pos
                : e instanceof Registry.Line l && !l.points.isEmpty() ? l.points.get(0)
                : null;
        if (e instanceof Registry.Area a && a.bounds() != null) {
            int[] b = a.bounds();
            at = new double[]{(b[0] + b[3] + 1) / 2.0, b[1] + 3, (b[2] + b[5] + 1) / 2.0};
        }
        if (at == null) return;
        Minecraft mc = Minecraft.getInstance();
        IntegratedServer server = mc.getSingleplayerServer();
        if (server == null || mc.player == null) return;
        UUID id = mc.player.getUUID();
        double x = at[0], y = at[1] + 1.5, z = at[2];
        float yaw = mc.player.getYRot(), pitch = mc.player.getXRot();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(id);
            if (sp != null) sp.teleportTo(sp.serverLevel(), x, y, z, yaw, pitch);
        });
        mode.select(e.id);
    }

    // -- characters ---------------------------------------------------------------

    private void characters() {
        Registry reg = store.get();
        List<Object[]> rows = new ArrayList<>();
        for (Registry.Character c : reg.characters) for (Registry.Variant v : c.variants) rows.add(new Object[]{c, v});
        int x = width / 2 - 160, y = 40;
        int from = page * rows();
        for (int i = from; i < Math.min(rows.size(), from + rows()); i++) {
            Registry.Character c = (Registry.Character) rows.get(i)[0];
            Registry.Variant v = (Registry.Variant) rows.get(i)[1];
            Button b = Button.builder(Component.literal(c.name + " / " + v.name + "  (" + v.body + ")"), z -> {
            }).bounds(x, y, 290, 20).build();
            if (!v.description.isBlank()) b.setTooltip(Tooltip.create(Component.literal(v.description)));
            addRenderableWidget(b);
            addRenderableWidget(Button.builder(Component.literal("✕"), z -> {
                store.checkpoint();
                c.variants.remove(v);
                if (c.variants.isEmpty()) reg.characters.remove(c);
                store.write();
                rebuildWidgets();
            }).tooltip(Tooltip.create(Component.translatable("slopcam.button.delete"))).bounds(x + 294, y, 26, 20).build());
            y += 22;
        }
        pager(x, height - 26, rows.size());
    }

    // -- props --------------------------------------------------------------------

    private void props() {
        Registry reg = store.get();
        int lx = width / 2 - 250, y = 40;
        search = new EditBox(font, lx, y, 200, 20, Component.translatable("slopcam.menu.search"));
        search.setHint(Component.translatable("slopcam.menu.search"));
        search.setValue(propQuery);
        search.setResponder(q -> {
            propQuery = q;
            fillItems();
        });
        addRenderableWidget(search);
        fillItems();
        int py = 40 + 26 + 6 * 18 + 8;
        propName = new EditBox(font, lx, py, 200, 20, Component.translatable("slopcam.menu.prop_name"));
        propName.setHint(Component.translatable("slopcam.menu.prop_name"));
        addRenderableWidget(propName);
        addRenderableWidget(Button.builder(Component.translatable("slopcam.menu.add_prop"), z -> {
            if (pickedItem == null || propName.getValue().isBlank() || minecraft.level == null) return;
            store.checkpoint();
            Registry.Prop p = new Registry.Prop();
            p.name = propName.getValue().trim();
            p.item = new ItemStack(pickedItem).save(minecraft.level.registryAccess()).toString();
            reg.props.removeIf(o -> o.name.equals(p.name));
            reg.props.add(p);
            store.write();
            rebuildWidgets();
        }).bounds(lx, py + 24, 200, 20).build());

        int rx = width / 2 - 30;
        int from = page * rows();
        for (int i = from; i < Math.min(reg.props.size(), from + rows()); i++) {
            Registry.Prop p = reg.props.get(i);
            addRenderableWidget(Button.builder(Component.literal(p.name), z -> {
            }).tooltip(Tooltip.create(Component.literal(p.item))).bounds(rx, y, 220, 20).build());
            addRenderableWidget(Button.builder(Component.literal("✕"), z -> {
                store.checkpoint();
                reg.props.remove(p);
                store.write();
                rebuildWidgets();
            }).bounds(rx + 224, y, 26, 20).build());
            y += 22;
        }
        pager(rx, height - 26, reg.props.size());
    }

    private void fillItems() {
        itemSlots.clear();
        String q = propQuery.toLowerCase(Locale.ROOT).trim();
        int lx = width / 2 - 250, y0 = 40 + 26;
        int n = 0;
        for (Item item : BuiltInRegistries.ITEM) {
            if (n >= 66) break;
            String id = BuiltInRegistries.ITEM.getKey(item).toString();
            String shown = new ItemStack(item).getHoverName().getString().toLowerCase(Locale.ROOT);
            if (!q.isEmpty() && !id.contains(q) && !shown.contains(q)) continue;
            itemSlots.add(new ItemSlot(lx + (n % 11) * 18, y0 + (n / 11) * 18, item));
            n++;
        }
    }

    @Override
    public boolean mouseClicked(double mx, double my, int button) {
        if (tab == Tab.PROPS) {
            for (ItemSlot s : itemSlots) {
                if (mx >= s.x && mx < s.x + 18 && my >= s.y && my < s.y + 18) {
                    pickedItem = s.item;
                    if (propName != null && propName.getValue().isBlank()) {
                        propName.setValue(new ItemStack(s.item).getHoverName().getString());
                    }
                    return true;
                }
            }
        }
        return super.mouseClicked(mx, my, button);
    }

    // -- settings -------------------------------------------------------------------

    private void settings() {
        SlopcamConfig cfg = SlopcamClient.config();
        int x = width / 2 - 150, y = 44;
        addRenderableWidget(CycleButton.onOffBuilder(cfg.seeThroughGlass)
                .create(x, y, 300, 20, Component.translatable("slopcam.settings.glass"), (b, v) -> {
                    cfg.seeThroughGlass = v;
                    cfg.save();
                }));
        y += 24;
        addRenderableWidget(CycleButton.<Integer>builder(v -> Component.literal(String.valueOf(v)))
                .withValues(0, 1, 2, 3).withInitialValue(cfg.brushRadius)
                .create(x, y, 300, 20, Component.translatable("slopcam.settings.brush"), (b, v) -> {
                    cfg.brushRadius = v;
                    cfg.save();
                }));
        y += 24;
        addRenderableWidget(CycleButton.<Integer>builder(v -> Component.literal(String.valueOf(v)))
                .withValues(1, 2, 3, 4, 5, 6, 8).withInitialValue(cfg.polygonHeight)
                .create(x, y, 300, 20, Component.translatable("slopcam.settings.polygon_height"), (b, v) -> {
                    cfg.polygonHeight = v;
                    cfg.save();
                }));
    }

    @Override
    public void render(GuiGraphics g, int mx, int my, float pt) {
        super.render(g, mx, my, pt);
        if (tab == Tab.PROPS) {
            for (ItemSlot s : itemSlots) {
                if (s.item == pickedItem) g.fill(s.x, s.y, s.x + 18, s.y + 18, 0x8060A0FF);
                g.renderItem(new ItemStack(s.item), s.x + 1, s.y + 1);
            }
            for (ItemSlot s : itemSlots) {
                if (mx >= s.x && mx < s.x + 18 && my >= s.y && my < s.y + 18) {
                    g.renderTooltip(font, new ItemStack(s.item), mx, my);
                }
            }
        }
        if (tab == Tab.ELEMENTS) {
            Registry.Place cur = mode.place();
            g.drawCenteredString(font, Component.translatable("slopcam.hud.place", cur == null ? "—" : cur.name), width / 2, 30, 0xA0A0A0);
        }
        if (tab == Tab.CHARACTERS) {
            g.drawCenteredString(font, Component.translatable("slopcam.menu.characters_hint"), width / 2, 30, 0xA0A0A0);
        }
        if (tab == Tab.SETTINGS) {
            g.drawCenteredString(font, Component.translatable("slopcam.settings.port", SlopcamClient.config().port,
                    SlopcamClient.control().connections()), width / 2, 30, 0xA0A0A0);
        }
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}
