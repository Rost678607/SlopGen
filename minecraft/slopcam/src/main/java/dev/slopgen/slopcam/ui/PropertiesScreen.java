package dev.slopgen.slopcam.ui;

import dev.slopgen.slopcam.markup.MarkupMode;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Checkbox;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.MultiLineEditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What a drawn shape IS: its roles (more than one if it is more than one thing — a
 * door is a landmark and an entrance), its name, its description and its place. The
 * name is what a direction will call it and the description is what the director is
 * told about it, so both are worth a sentence.
 */
public final class PropertiesScreen extends Screen {
    private static final Map<String, List<String>> ROLES = Map.of(
            "area", List.of("zone", "forbidden"),
            "point", List.of("landmark", "seat", "entry"),
            "line", List.of("route", "queue", "rail"),
            "camera", List.of());

    private final MarkupMode mode;
    private final RegistryStore store;
    private final Registry.Element element;
    private EditBox name;
    private MultiLineEditBox description;
    private final Map<String, Checkbox> roles = new LinkedHashMap<>();
    private CycleButton<Registry.Place> place;
    private CycleButton<Integer> fov;
    private CycleButton<String> rail;

    public PropertiesScreen(MarkupMode mode, RegistryStore store, Registry.Element element) {
        super(Component.translatable("slopcam.props.title"));
        this.mode = mode;
        this.store = store;
        this.element = element;
    }

    @Override
    protected void init() {
        Registry reg = store.get();
        int cx = width / 2, w = 300, x = cx - w / 2;
        int y = 30;
        name = new EditBox(font, x, y, w, 20, Component.translatable("slopcam.props.name"));
        name.setMaxLength(64);
        name.setValue(element.name);
        addRenderableWidget(name);
        y += 30;
        description = new MultiLineEditBox(font, x, y, w, 60,
                Component.translatable("slopcam.props.description_hint"), Component.translatable("slopcam.props.description"));
        description.setValue(element.description);
        addRenderableWidget(description);
        y += 70;

        List<String> possible = ROLES.getOrDefault(element.kind(), List.of());
        int rx = x;
        for (String role : possible) {
            Checkbox box = Checkbox.builder(Component.translatable("slopcam.role." + role), font)
                    .pos(rx, y).selected(element.roles.contains(role)).build();
            roles.put(role, box);
            addRenderableWidget(box);
            rx += 100;
        }
        if (!possible.isEmpty()) y += 26;

        List<Registry.Place> places = new ArrayList<>(reg.places);
        Registry.Place current = reg.place(element.place);
        if (!places.isEmpty()) {
            place = CycleButton.<Registry.Place>builder(p -> Component.literal(p.name))
                    .withValues(places).withInitialValue(current != null ? current : places.get(0))
                    .create(x, y, w, 20, Component.translatable("slopcam.props.place"));
            addRenderableWidget(place);
            y += 26;
        }

        if (element instanceof Registry.Camera c) {
            List<Integer> fovs = List.of(20, 30, 40, 50, 60, 70, 80, 90, 100, 110);
            int nearest = fovs.stream().min((a, b) -> Float.compare(Math.abs(a - c.fov), Math.abs(b - c.fov))).orElse(70);
            fov = CycleButton.<Integer>builder(v -> Component.literal(v + "°")).withValues(fovs).withInitialValue(nearest)
                    .create(x, y, w / 2 - 2, 20, Component.translatable("slopcam.props.fov"));
            addRenderableWidget(fov);
            List<String> rails = new ArrayList<>();
            rails.add("");
            for (Registry.Line l : reg.lines) if (l.place.equals(c.place) && l.roles.contains("rail")) rails.add(l.id);
            rail = CycleButton.<String>builder(id -> id.isEmpty()
                            ? Component.translatable("slopcam.props.fixed")
                            : Component.literal(reg.byId(id) == null ? id : reg.byId(id).name))
                    .withValues(rails).withInitialValue(rails.contains(c.rail) ? c.rail : "")
                    .create(x + w / 2 + 2, y, w / 2 - 2, 20, Component.translatable("slopcam.props.rail"));
            addRenderableWidget(rail);
            y += 26;
        }

        y += 6;
        addRenderableWidget(Button.builder(Component.translatable("slopcam.button.save"), b -> save())
                .bounds(x, y, 96, 20).build());
        addRenderableWidget(Button.builder(Component.translatable("slopcam.button.delete"), b -> delete())
                .bounds(x + 102, y, 96, 20).build());
        addRenderableWidget(Button.builder(Component.translatable("gui.cancel"), b -> onClose())
                .bounds(x + 204, y, 96, 20).build());
        setInitialFocus(name);
    }

    private void save() {
        store.checkpoint();
        element.name = name.getValue().isBlank() ? element.name : name.getValue().trim();
        element.description = description.getValue().trim();
        if (!roles.isEmpty()) {
            element.roles.clear();
            roles.forEach((role, box) -> {
                if (box.selected()) element.roles.add(role);
            });
            if (element.roles.isEmpty()) element.roles.add(roles.keySet().iterator().next());
        }
        if (place != null) element.place = place.getValue().id;
        if (element instanceof Registry.Camera c) {
            if (fov != null) c.fov = fov.getValue();
            if (rail != null) {
                c.rail = rail.getValue();
                c.kind = c.rail.isEmpty() ? "fixed" : "rail";
            }
        }
        store.write();
        onClose();
    }

    private void delete() {
        store.checkpoint();
        store.get().remove(element);
        mode.select("");
        store.write();
        onClose();
    }

    @Override
    public void render(GuiGraphics g, int mx, int my, float pt) {
        super.render(g, mx, my, pt);
        g.drawCenteredString(font, Component.translatable("slopcam.kind." + element.kind()), width / 2, 12, 0xFFFFFF);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}
