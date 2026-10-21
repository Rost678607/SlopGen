package dev.slopgen.slopcam.stage;

import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * One scene as slopgen compiled it ({@code slopgen.minecraft.compile}): who stands
 * where with which face, what happens on which tick, and how every body part moves.
 * Everything with meaning — dialogue, crowds, gestures — was decided there; this is
 * only ticks and coordinates.
 */
public final class Script {
    public int scene;
    public String place = "";
    public int time = 6000;
    public String weather = "clear";
    public int preroll;
    public int ticks;
    public double[] focus = {0, 0, 0};
    public List<ActorSpec> actors = new ArrayList<>();
    public List<Event> events = new ArrayList<>();
    public Map<String, Body> bodies = new HashMap<>();

    public static final class ActorSpec {
        public String id = "";
        public String face = "";
        public String preset = "";
        public String body = "humanoid";
        public double[] pos = {0, 0, 0};
        public float yaw;
        public float scale = 1f;
        public Map<String, String> equip = new HashMap<>();
        public boolean hidden;
        public boolean extra;
    }

    /** A target: another actor, or a position. */
    public static final class Target {
        public String actor;
        public double[] pos;
    }

    public static final class Event {
        public int tick;
        public String actor = "";
        public String op = "";
        public Target target;
        public double speed = 1.0;
        public Integer until;
        public JsonObject then;
        public List<double[]> via = new ArrayList<>();
        public String slot = "";
        public String item = "";
        public String name = "";
        public double[] pos;
    }

    /** Dense per-part tracks: [tick, [x, y, z]] in degrees; offsets in blocks. */
    public static final class Body {
        public Map<String, List<Object[]>> parts = new HashMap<>();
        public List<Object[]> offset = new ArrayList<>();
        public List<Object[]> vanilla = new ArrayList<>();
    }
}
