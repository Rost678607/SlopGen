package dev.slopgen.slopcam.registry;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * The world's markup, field for field the JSON that slopgen reads
 * ({@code slopgen.minecraft.registry}). Two programs write this file — the markup
 * mode here and the browser there — so the shape is the contract and neither side
 * adds a field the other would drop on its next save.
 *
 * <p>A place is a group, not a shape: elements name the place they belong to. What
 * an element IS lives in its roles, set after it is drawn, and an element may have
 * several.
 */
public final class Registry {
    public int version = 1;
    public List<Place> places = new ArrayList<>();
    public List<Area> areas = new ArrayList<>();
    public List<Point> points = new ArrayList<>();
    public List<Line> lines = new ArrayList<>();
    public List<Camera> cameras = new ArrayList<>();
    public List<Character> characters = new ArrayList<>();
    public List<Prop> props = new ArrayList<>();

    public static String newId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 10);
    }

    public static class Place {
        public String id = newId();
        public String name = "";
        public String description = "";
        public String screenshot = "";
    }

    public static class Element {
        public String id = newId();
        public String place = "";
        public String name = "";
        public String description = "";
        public List<String> roles = new ArrayList<>();

        public String kind() {
            return "element";
        }
    }

    /** One piece of an area, added or cut; pieces apply in order. Cells are FEET cells. */
    public static final class Piece {
        public String op = "add";
        public String kind = "box";
        public int[] a;
        public int[] b;
        public List<double[]> points = new ArrayList<>();
        public int y0;
        public int y1;
        public List<int[]> cells = new ArrayList<>();

        private transient Set<Long> cellSet;

        public boolean contains(int x, int y, int z) {
            switch (kind) {
                case "box":
                    if (a == null || b == null) return false;
                    return between(x, a[0], b[0]) && between(y, a[1], b[1]) && between(z, a[2], b[2]);
                case "polygon":
                    return between(y, y0, y1) && inPolygon(x + 0.5, z + 0.5);
                case "cells":
                    if (cellSet == null || cellSet.size() != cells.size()) {
                        cellSet = new HashSet<>();
                        for (int[] c : cells) cellSet.add(pack(c[0], c[1], c[2]));
                    }
                    return cellSet.contains(pack(x, y, z));
                default:
                    return false;
            }
        }

        public void touched() {
            cellSet = null;
        }

        private boolean inPolygon(double x, double z) {
            boolean inside = false;
            int n = points.size();
            for (int i = 0; i < n; i++) {
                double[] p = points.get(i), q = points.get((i + 1) % n);
                if ((p[1] > z) != (q[1] > z)) {
                    double cross = p[0] + (z - p[1]) * (q[0] - p[0]) / (q[1] - p[1]);
                    if (x < cross) inside = !inside;
                }
            }
            return inside;
        }

        /** Inclusive bounds as {minX, minY, minZ, maxX, maxY, maxZ}, or null. */
        public int[] bounds() {
            switch (kind) {
                case "box":
                    if (a == null || b == null) return null;
                    return new int[]{Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.min(a[2], b[2]),
                            Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.max(a[2], b[2])};
                case "polygon": {
                    if (points.isEmpty()) return null;
                    double x0 = Double.MAX_VALUE, z0 = Double.MAX_VALUE, x1 = -Double.MAX_VALUE, z1 = -Double.MAX_VALUE;
                    for (double[] p : points) {
                        x0 = Math.min(x0, p[0]); z0 = Math.min(z0, p[1]);
                        x1 = Math.max(x1, p[0]); z1 = Math.max(z1, p[1]);
                    }
                    return new int[]{(int) Math.floor(x0) - 1, Math.min(y0, y1), (int) Math.floor(z0) - 1,
                            (int) Math.floor(x1) + 1, Math.max(y0, y1), (int) Math.floor(z1) + 1};
                }
                case "cells": {
                    if (cells.isEmpty()) return null;
                    int[] r = {Integer.MAX_VALUE, Integer.MAX_VALUE, Integer.MAX_VALUE,
                            Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE};
                    for (int[] c : cells) {
                        for (int i = 0; i < 3; i++) {
                            r[i] = Math.min(r[i], c[i]);
                            r[i + 3] = Math.max(r[i + 3], c[i]);
                        }
                    }
                    return r;
                }
                default:
                    return null;
            }
        }

        private static boolean between(int v, int p, int q) {
            return Math.min(p, q) <= v && v <= Math.max(p, q);
        }
    }

    public static long pack(int x, int y, int z) {
        return ((long) (x & 0x3FFFFFF) << 38) | ((long) (z & 0x3FFFFFF) << 12) | (y & 0xFFF);
    }

    public static final class Area extends Element {
        public List<Piece> pieces = new ArrayList<>();

        public Area() {
            roles.add("zone");
        }

        @Override
        public String kind() {
            return "area";
        }

        public boolean contains(int x, int y, int z) {
            boolean inside = false;
            for (Piece p : pieces) {
                if (p.contains(x, y, z)) inside = p.op.equals("add");
            }
            return inside;
        }

        public int[] bounds() {
            int[] r = null;
            for (Piece p : pieces) {
                if (!p.op.equals("add")) continue;
                int[] b = p.bounds();
                if (b == null) continue;
                if (r == null) {
                    r = b.clone();
                } else {
                    for (int i = 0; i < 3; i++) {
                        r[i] = Math.min(r[i], b[i]);
                        r[i + 3] = Math.max(r[i + 3], b[i + 3]);
                    }
                }
            }
            return r;
        }

        /** Every feet cell inside, as {x, y, z}; bounded so a runaway area cannot hang a frame. */
        public List<int[]> cells(int limit) {
            List<int[]> out = new ArrayList<>();
            int[] b = bounds();
            if (b == null) return out;
            for (int y = b[1]; y <= b[4]; y++)
                for (int z = b[2]; z <= b[5]; z++)
                    for (int x = b[0]; x <= b[3]; x++)
                        if (contains(x, y, z)) {
                            out.add(new int[]{x, y, z});
                            if (out.size() >= limit) return out;
                        }
            return out;
        }
    }

    public static final class Point extends Element {
        public double[] pos = {0, 0, 0};
        public float yaw;

        public Point() {
            roles.add("landmark");
        }

        @Override
        public String kind() {
            return "point";
        }
    }

    public static final class Line extends Element {
        public List<double[]> points = new ArrayList<>();

        public Line() {
            roles.add("route");
        }

        @Override
        public String kind() {
            return "line";
        }
    }

    public static final class Camera extends Element {
        public String kind = "fixed";
        public double[] pos = {0, 0, 0};
        public float yaw;
        public float pitch;
        public float fov = 70f;
        public String rail = "";
        public String screenshot = "";

        @Override
        public String kind() {
            return "camera";
        }
    }

    public static final class Variant {
        public String name = "";
        public String preset = "";
        public String body = "humanoid";
        public String description = "";
    }

    public static final class Character {
        public String name = "";
        public List<Variant> variants = new ArrayList<>();
    }

    public static final class Prop {
        public String name = "";
        public String item = "";
        public String description = "";
    }

    // -- lookups --------------------------------------------------------------

    public Place place(String id) {
        for (Place p : places) if (p.id.equals(id)) return p;
        return null;
    }

    public List<Element> elements(String placeId) {
        List<Element> out = new ArrayList<>();
        for (Element e : all()) if (e.place.equals(placeId)) out.add(e);
        return out;
    }

    public List<Element> all() {
        List<Element> out = new ArrayList<>();
        out.addAll(areas);
        out.addAll(points);
        out.addAll(lines);
        out.addAll(cameras);
        return out;
    }

    public Element byId(String id) {
        for (Element e : all()) if (e.id.equals(id)) return e;
        return null;
    }

    public boolean remove(Element e) {
        return areas.remove(e) || points.remove(e) || lines.remove(e) || cameras.remove(e);
    }

    public Character character(String name) {
        for (Character c : characters) if (c.name.equals(name)) return c;
        return null;
    }

    /** A name not yet used in this place: "зона", "зона 2", … */
    public String freeName(String placeId, String base) {
        Set<String> taken = new HashSet<>();
        for (Element e : elements(placeId)) taken.add(e.name);
        if (!taken.contains(base)) return base;
        for (int i = 2; ; i++) if (!taken.contains(base + " " + i)) return base + " " + i;
    }
}
