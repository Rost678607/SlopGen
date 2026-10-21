package dev.slopgen.slopcam.net;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.slopgen.slopcam.Json;
import dev.slopgen.slopcam.SlopcamClient;
import net.minecraft.client.Minecraft;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Function;

/**
 * The line slopgen talks to the game on: newline-delimited JSON over a localhost
 * socket.
 *
 * <p>It lives in the CLIENT, not in the integrated server, because the client is the
 * one thing that survives everything a shoot does — leaving the world for a
 * Flashback replay and coming back would take an integrated-server socket down with
 * it. Only 127.0.0.1 is bound: this is a remote control for the operator's own game.
 *
 * <p>A request is {@code {"id", "method", "params"}} and gets exactly one
 * {@code {"id", "ok", "result" | "error"}}. Handlers run on the client thread (the
 * only thread allowed to touch the game) and may finish later — a take that is being
 * played answers when it is done — so each returns a future. Anything the game wants
 * to say unprompted goes to every connection as {@code {"event", "data"}}.
 */
public final class ControlServer implements Events {
    private final int port;
    private final Map<String, Function<JsonObject, CompletableFuture<JsonElement>>> methods = new ConcurrentHashMap<>();
    private final List<Socket> clients = new CopyOnWriteArrayList<>();
    private ServerSocket server;

    public ControlServer(int port) {
        this.port = port;
    }

    public void handle(String method, Function<JsonObject, CompletableFuture<JsonElement>> fn) {
        methods.put(method, fn);
    }

    /** A handler that answers at once on the client thread. */
    public void handleNow(String method, Function<JsonObject, JsonElement> fn) {
        methods.put(method, p -> CompletableFuture.completedFuture(fn.apply(p)));
    }

    public void start() {
        Thread t = new Thread(this::acceptLoop, "slopcam-control");
        t.setDaemon(true);
        t.start();
    }

    private void acceptLoop() {
        try (ServerSocket s = new ServerSocket(port, 8, InetAddress.getLoopbackAddress())) {
            server = s;
            SlopcamClient.LOG.info("slopcam: listening on 127.0.0.1:{}", port);
            while (!s.isClosed()) {
                Socket c = s.accept();
                clients.add(c);
                Thread r = new Thread(() -> readLoop(c), "slopcam-conn");
                r.setDaemon(true);
                r.start();
            }
        } catch (IOException e) {
            SlopcamClient.LOG.error("slopcam: control socket on port {} failed", port, e);
        }
    }

    private void readLoop(Socket c) {
        try (BufferedReader in = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isBlank()) continue;
                dispatch(c, line);
            }
        } catch (IOException ignored) {
        } finally {
            clients.remove(c);
            try {
                c.close();
            } catch (IOException ignored) {
            }
        }
    }

    private void dispatch(Socket c, String line) {
        JsonObject req;
        try {
            req = JsonParser.parseString(line).getAsJsonObject();
        } catch (Exception e) {
            reply(c, null, null, "not JSON: " + e.getMessage());
            return;
        }
        JsonElement id = req.get("id");
        String method = req.has("method") ? req.get("method").getAsString() : "";
        JsonObject params = req.has("params") && req.get("params").isJsonObject() ? req.getAsJsonObject("params") : new JsonObject();
        Function<JsonObject, CompletableFuture<JsonElement>> fn = methods.get(method);
        if (fn == null) {
            reply(c, id, null, "unknown method " + method);
            return;
        }
        CompletableFuture<JsonElement> done = new CompletableFuture<>();
        Minecraft.getInstance().execute(() -> {
            try {
                fn.apply(params).whenComplete((r, err) -> {
                    if (err != null) done.completeExceptionally(err);
                    else done.complete(r);
                });
            } catch (Throwable e) {
                done.completeExceptionally(e);
            }
        });
        done.whenComplete((r, err) -> reply(c, id, r, err == null ? null : message(err)));
    }

    private static String message(Throwable e) {
        while (e.getCause() != null && (e.getMessage() == null || e instanceof java.util.concurrent.CompletionException)) {
            e = e.getCause();
        }
        return e.getMessage() == null ? e.toString() : e.getMessage();
    }

    private void reply(Socket c, JsonElement id, JsonElement result, String error) {
        JsonObject out = new JsonObject();
        out.add("id", id);
        out.addProperty("ok", error == null);
        if (error == null) out.add("result", result);
        else out.addProperty("error", error);
        send(c, out);
    }

    @Override
    public void push(String event, Object data) {
        JsonObject out = new JsonObject();
        out.addProperty("event", event);
        out.add("data", Json.GSON.toJsonTree(data));
        for (Socket c : clients) send(c, out);
    }

    private void send(Socket c, JsonObject msg) {
        byte[] bytes = (Json.GSON.toJson(msg) + "\n").getBytes(StandardCharsets.UTF_8);
        synchronized (c) {
            try {
                OutputStream o = c.getOutputStream();
                o.write(bytes);
                o.flush();
            } catch (IOException e) {
                clients.remove(c);
            }
        }
    }

    public int connections() {
        return clients.size();
    }
}
