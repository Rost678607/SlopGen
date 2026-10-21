package dev.slopgen.slopcam;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

/** One Gson for everything slopcam reads and writes. */
public final class Json {
    public static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();
    public static final Gson PRETTY = new GsonBuilder().disableHtmlEscaping().setPrettyPrinting().serializeNulls().create();

    private Json() {
    }
}
