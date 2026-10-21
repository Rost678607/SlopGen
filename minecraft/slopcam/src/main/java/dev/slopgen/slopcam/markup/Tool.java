package dev.slopgen.slopcam.markup;

import net.minecraft.world.item.Item;
import net.minecraft.world.item.Items;

/**
 * The nine slots of the markup hotbar. They are not items: the mod draws them and
 * handles their clicks, so they cannot be dropped, moved to the other hand or lost,
 * and the player's real inventory is never touched (see {@link MarkupMode}).
 *
 * <p>The drawing tools only draw. What a shape IS — a zone, a seat, a queue — is set
 * afterwards with {@link #PROPERTIES}.
 */
public enum Tool {
    SELECT("select", Items.ARROW),
    BOX("box", Items.SCAFFOLDING),
    POLYGON("polygon", Items.STRING),
    BRUSH("brush", Items.BRUSH),
    LINE("line", Items.LEAD),
    POINT("point", Items.TORCH),
    CAMERA("camera", Items.SPYGLASS),
    PROPERTIES("properties", Items.WRITABLE_BOOK),
    CHECK("check", Items.ARMOR_STAND);

    public final String key;
    public final Item icon;

    Tool(String key, Item icon) {
        this.key = key;
        this.icon = icon;
    }

    public String nameKey() {
        return "slopcam.tool." + key;
    }

    public String hintKey() {
        return "slopcam.tool." + key + ".hint";
    }
}
