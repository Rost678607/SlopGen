package dev.slopgen.slopcam.net;

/** Something slopcam says to slopgen without being asked. */
public interface Events {
    void push(String event, Object data);

    Events NONE = (event, data) -> {
    };
}
