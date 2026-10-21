package dev.slopgen.slopcam.ui;

import dev.slopgen.slopcam.SlopcamClient;
import dev.slopgen.slopcam.registry.Registry;
import dev.slopgen.slopcam.registry.RegistryStore;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.MultiLineEditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

/**
 * Makes an NPC standing in the world a face of a character: «this is Повариха, the
 * older one». What is kept is the whole entity as Easy NPC saved it — skin, model,
 * scale, equipment — minus where it stands and who it is, so a scene can put a copy
 * of it anywhere. The NPC itself is left alone.
 *
 * <p>A character in the markup is the fandom's character (or estate) by name, and a
 * face is one of its variants; a main actor names the face, a crowd mixes faces.
 */
public final class ActorScreen extends Screen {
    private final RegistryStore store;
    private final UUID entity;
    private final String type;
    private EditBox character;
    private EditBox variant;
    private MultiLineEditBox description;

    public ActorScreen(RegistryStore store, LivingEntity entity) {
        super(Component.translatable("slopcam.actor.title"));
        this.store = store;
        this.entity = entity.getUUID();
        this.type = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).getPath();
    }

    @Override
    protected void init() {
        int w = 300, x = width / 2 - w / 2, y = 40;
        character = new EditBox(font, x, y, w, 20, Component.translatable("slopcam.actor.character"));
        character.setHint(Component.translatable("slopcam.actor.character"));
        addRenderableWidget(character);
        y += 26;
        int bx = x;
        for (Registry.Character c : store.get().characters) {
            if (bx + 70 > x + w) break;
            String n = c.name;
            addRenderableWidget(Button.builder(Component.literal(n), b -> character.setValue(n)).bounds(bx, y, 68, 16).build());
            bx += 72;
        }
        if (bx != x) y += 22;
        variant = new EditBox(font, x, y, w, 20, Component.translatable("slopcam.actor.variant"));
        variant.setHint(Component.translatable("slopcam.actor.variant"));
        addRenderableWidget(variant);
        y += 26;
        description = new MultiLineEditBox(font, x, y, w, 50, Component.translatable("slopcam.actor.description_hint"),
                Component.translatable("slopcam.props.description"));
        addRenderableWidget(description);
        y += 60;
        addRenderableWidget(Button.builder(Component.translatable("slopcam.button.save"), b -> save()).bounds(x, y, 146, 20).build());
        addRenderableWidget(Button.builder(Component.translatable("gui.cancel"), b -> onClose()).bounds(x + 154, y, 146, 20).build());
        setInitialFocus(character);
    }

    private void save() {
        String ch = character.getValue().trim(), var = variant.getValue().trim();
        if (ch.isEmpty() || var.isEmpty() || store.dir() == null) return;
        String desc = description.getValue().trim();
        String rel = "actors/" + safe(ch) + "/" + safe(var) + ".snbt";
        Path file = store.dir().resolve(rel);
        IntegratedServer server = Minecraft.getInstance().getSingleplayerServer();
        if (server == null) return;
        server.execute(() -> {
            Entity e = null;
            for (ServerLevel level : server.getAllLevels()) {
                e = level.getEntity(entity);
                if (e != null) break;
            }
            if (e == null) return;
            CompoundTag tag = e.saveWithoutId(new CompoundTag());
            // where it stands and who it is belong to this NPC, not to the face
            for (String k : new String[]{"UUID", "Pos", "Motion", "Rotation", "FallDistance", "OnGround", "PortalCooldown", "Leash"}) {
                tag.remove(k);
            }
            tag.putString("id", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            String snbt = tag.toString();
            Minecraft.getInstance().execute(() -> {
                try {
                    Files.createDirectories(file.getParent());
                    Files.writeString(file, snbt, StandardCharsets.UTF_8);
                } catch (Exception ex) {
                    SlopcamClient.LOG.error("slopcam: cannot write {}", file, ex);
                    return;
                }
                store.checkpoint();
                Registry reg = store.get();
                Registry.Character c = reg.character(ch);
                if (c == null) {
                    c = new Registry.Character();
                    c.name = ch;
                    reg.characters.add(c);
                }
                c.variants.removeIf(v -> v.name.equals(var));
                Registry.Variant v = new Registry.Variant();
                v.name = var;
                v.preset = rel;
                v.body = type;
                v.description = desc;
                c.variants.add(v);
                store.write();
            });
        });
        onClose();
    }

    private static String safe(String s) {
        return s.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    @Override
    public void render(GuiGraphics g, int mx, int my, float pt) {
        super.render(g, mx, my, pt);
        g.drawCenteredString(font, title, width / 2, 12, 0xFFFFFF);
        g.drawCenteredString(font, Component.translatable("slopcam.actor.body", type), width / 2, 24, 0xA0A0A0);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}
