"""Machinima core: markup, direction, anchors, crowds and the camera search — all of it
plain data and geometry, tested without a game or a model."""

import math
import random

import pytest

from slopgen.minecraft import anchors, camera, crowd, easing
from slopgen.minecraft.direction import Anchor, Direction, WordRef, validate
from slopgen.minecraft.registry import (
    Area, Camera, Character, Line, Piece, Place, Point, Prop, Registry, Variant,
)
from slopgen.minecraft.voxels import World, traverse


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def canteen() -> Registry:
    place = Place(name="Буфет", description="буфет третьего этажа")
    pid = place.id
    return Registry(
        places=[place],
        areas=[Area(place=pid, name="зал", pieces=[Piece(kind="box", a=(0, 1, 0), b=(9, 1, 9))]),
               Area(place=pid, name="за стойкой", roles=["forbidden"],
                    pieces=[Piece(kind="box", a=(0, 1, 8), b=(9, 1, 9))])],
        points=[Point(place=pid, name="стойка", pos=(5.5, 1, 7.5), yaw=0),
                Point(place=pid, name="касса", pos=(7.5, 1, 7.5)),
                Point(place=pid, name="дверь", roles=["landmark", "entry"], pos=(0.5, 1, 0.5)),
                Point(place=pid, name="стул_1", roles=["seat"], pos=(2.5, 1, 2.5), yaw=90),
                Point(place=pid, name="стул_2", roles=["seat"], pos=(3.5, 1, 2.5), yaw=90)],
        lines=[Line(place=pid, name="очередь", roles=["queue"],
                    points=[(7.5, 1, 6.5), (7.5, 1, 1.5)])],
        cameras=[Camera(place=pid, name="из-за стойки", pos=(5.5, 2.6, 8.8), fov=60)],
        characters=[Character(name="Повариха", variants=[Variant(name="старшая")]),
                    Character(name="Практиканты", variants=[Variant(name="рыжий"), Variant(name="очкарик"),
                                                            Variant(name="высокий")]),
                    Character(name="Табельщицы", variants=[Variant(name="а"), Variant(name="б")])],
        props=[Prop(name="половник", item="minecraft:iron_shovel")],
    )


def floor_world(size=12) -> World:
    w = World.empty((-1, 0, -1), (size + 2, 6, size + 2))
    for x in range(-1, size + 1):
        for z in range(-1, size + 1):
            w.set((x, 0, z))
    return w


def ref(scene, i, text=""):
    return WordRef(scene=scene, i=i, text=text)


def good_direction() -> dict:
    return {"scenes": [{
        "place": "Буфет", "time": "day",
        "cast": [{"as": "a", "actor": "Повариха/старшая", "at": "стойка", "hold": "половник"},
                 {"as": "b", "actor": "Практиканты/рыжий", "at": "дверь", "facing": "a"}],
        "crowd": [{"in": "зал", "density": 0.3, "mix": {"Практиканты": 0.6, "Табельщицы/*": 0.4},
                   "exclude": ["Практиканты/очкарик"], "do": "talk_groups"}],
        "action": [
            {"at": {"word": {"scene": 0, "i": 1, "text": "подошёл"}}, "who": "b", "do": "walk", "to": "касса"},
            {"at": {"word": {"scene": 0, "i": 3}}, "do": "dialogue", "who": ["a", "b"],
             "turns": [{"speaker": "b", "until": {"word": {"scene": 0, "i": 5}}}, {"speaker": "a"}]},
            {"at": {"between": [{"scene": 1, "i": 0}, {"scene": 1, "i": 2}], "frac": 0.4},
             "who": "a", "do": "gesture", "name": "кивок", "ease": "out"},
        ],
        "shots": [
            {"at": {"word": {"scene": 0, "i": 1}}, "camera": {"on": "b", "side": "back", "distance": [4, 2.5]}},
            {"at": {"word": {"scene": 0, "i": 3}}, "camera": {"style": "dialogue", "pair": ["a", "b"], "distance": 1.8}},
            {"at": {"word": {"scene": 1, "i": 2}}, "camera": {"fixed": "из-за стойки", "aim": "a", "fov": [70, 50]}},
        ],
    }]}


# --------------------------------------------------------------------------
# easing
# --------------------------------------------------------------------------


def test_easing_endpoints_and_step():
    for kind in easing.EASES:
        assert easing.ease(kind, 1.0) == pytest.approx(1.0)
        if kind != "step":
            assert easing.ease(kind, 0.0) == pytest.approx(0.0)
    assert easing.ease("step", 0.99) == 0.0  # interpolation OFF: hold, then jump
    assert max(easing.ease("overshoot", t / 20) for t in range(21)) > 1.0


def test_sample_takes_the_short_way_round():
    keys = [(0.0, 170.0, "linear"), (1.0, -170.0, "linear")]
    assert easing.sample(keys, 0.5, angle=True) % 360 == pytest.approx(180.0)
    assert easing.sample(keys, -1) == 170.0 and easing.sample(keys, 5) == -170.0


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def test_area_pieces_add_and_cut_in_order():
    a = Area(name="L", pieces=[
        Piece(kind="box", a=(0, 0, 0), b=(4, 0, 4)),
        Piece(op="cut", kind="box", a=(2, 0, 2), b=(4, 0, 4)),
        Piece(kind="cells", cells=[(3, 0, 3)]),
    ])
    assert a.contains((0, 0, 0)) and not a.contains((2, 0, 2))
    assert a.contains((3, 0, 3))  # painted back in after the cut
    assert len(a.cells()) == 25 - 9 + 1


def test_polygon_piece():
    tri = Piece(kind="polygon", points=[(0, 0), (10, 0), (0, 10)], y0=1, y1=2)
    assert tri.contains((1, 1, 1)) and tri.contains((1, 2, 1))
    assert not tri.contains((8, 1, 8)) and not tri.contains((1, 3, 1))


def test_registry_roundtrip_and_lookups(tmp_path):
    reg = canteen()
    reg.save(tmp_path)
    back = Registry.load(tmp_path)
    assert back.place("Буфет").description == "буфет третьего этажа"
    p = back.place("Буфет")
    assert back.find(p, "касса", (Point,)) is not None
    assert back.variant("Повариха") is not None  # a single face may go unnamed
    assert back.variant("Практиканты") is None  # several: ambiguous for a main actor
    assert back.variant("Практиканты/очкарик")[1].name == "очкарик"


def test_line_at_fraction():
    ln = Line(name="l", points=[(0, 0, 0), (10, 0, 0), (10, 0, 10)])
    assert ln.at(0.5) == pytest.approx((10, 0, 0))
    assert ln.at(0.75) == pytest.approx((10, 0, 5))


# --------------------------------------------------------------------------
# direction
# --------------------------------------------------------------------------


def test_good_direction_validates():
    d = Direction.model_validate(good_direction())
    words = {(0, i) for i in range(8)} | {(1, i) for i in range(5)}
    assert validate(d, canteen(), words) == []


def test_validation_names_what_is_wrong_and_what_would_be_right():
    raw = good_direction()
    sc = raw["scenes"][0]
    sc["cast"][0]["at"] = "стол_3"
    sc["cast"][1]["actor"] = "Практиканты"  # ambiguous face
    sc["action"][0]["to"] = "окно"
    sc["action"].append({"at": {"sec": 1}, "who": "z", "do": "fly"})
    sc["shots"][2]["camera"]["fixed"] = "сверху"
    sc["crowd"][0]["mix"]["Директора"] = 1
    errs = validate(Direction.model_validate(raw), canteen())
    text = "\n".join(errs)
    assert "стол_3" in text and "стойка" in text  # the bad name, and the real ones
    assert "Практиканты/рыжий" in text  # faces listed for the ambiguous actor
    assert "окно" in text
    assert "unknown verb" in text
    assert "сверху" in text and "из-за стойки" in text
    assert "Директора" in text


def test_anchor_must_be_exactly_one_form_and_inside_the_block():
    raw = good_direction()
    raw["scenes"][0]["shots"][0]["at"] = {"word": {"scene": 0, "i": 1}, "sec": 2}
    raw["scenes"][0]["action"][0]["at"] = {"word": {"scene": 9, "i": 9, "text": "чужое"}}
    errs = validate(Direction.model_validate(raw), canteen(), words={(0, i) for i in range(8)} | {(1, i) for i in range(5)})
    assert any("exactly one" in e for e in errs)
    assert any("чужое" in e for e in errs)


# --------------------------------------------------------------------------
# anchors
# --------------------------------------------------------------------------


def clock() -> anchors.Clock:
    ws = [anchors.ClockWord(0, i, t, 10 + i, 10.8 + i) for i, t in enumerate("и он подошёл к кассе".split())]
    ws += [anchors.ClockWord(1, i, t, 20 + 2 * i, 21 + 2 * i) for i, t in enumerate("она только кивнула".split())]
    return anchors.Clock(ws)


def test_resolve_word_offset_between_and_seconds():
    c = clock()
    assert anchors.resolve(Anchor(word=ref(0, 2)), c, 10) == pytest.approx(2.0)
    assert anchors.resolve(Anchor(word=ref(0, 2), offset=-0.25), c, 10) == pytest.approx(1.75)
    assert anchors.resolve(Anchor(between=(ref(1, 0), ref(1, 2)), frac=0.25), c, 10) == pytest.approx(11.0)
    assert anchors.resolve(Anchor(sec=3.5), c, 10) == 3.5
    with pytest.raises(anchors.AnchorError):
        anchors.resolve(Anchor(word=ref(5, 0)), c, 10)


def test_rewritten_word_is_stale_but_a_moved_one_is_not():
    d = Direction.model_validate({"scenes": [{"place": "Буфет", "shots": [
        {"at": {"word": {"scene": 1, "i": 2, "text": "кивнула"}}, "camera": {"on": "a"}},
        {"at": {"word": {"scene": 0, "i": 2, "text": "ушёл"}}, "camera": {"on": "a"}}]}]})
    stale = anchors.stale_words(d, clock())
    assert [(r.scene, r.i) for r in stale] == [(0, 2)]


def test_clock_of_scenes_stretches_like_the_picture_track():
    from slopgen.pipeline.job import Scene, Word

    scenes = [Scene(text="a b", duration=4.0, audio_src_duration=2.0,
                    words=[Word(text="a", start=0.0, end=0.5), Word(text="b", start=1.0, end=1.5)]),
              Scene(text="c", duration=1.0, words=[Word(text="c", start=0.2, end=0.6)])]
    c = anchors.Clock.of(scenes)
    assert c.word(ref(0, 1)).start == pytest.approx(2.0)  # stretched ×2
    assert c.word(ref(1, 0)).start == pytest.approx(4.2)  # after the first scene


# --------------------------------------------------------------------------
# crowds
# --------------------------------------------------------------------------


def test_faces_spread_shares_and_honour_exclusions():
    reg = canteen()
    cr = Direction.model_validate(good_direction()).scenes[0].crowd[0]
    w = crowd.faces(cr, reg)
    assert "Практиканты/очкарик" not in w
    assert w["Практиканты/рыжий"] == pytest.approx(0.3)
    assert w["Табельщицы/а"] == pytest.approx(0.2)
    assert sum(w.values()) == pytest.approx(1.0)


def test_apportion_keeps_the_small_share():
    counts = crowd.apportion({"a": 0.6, "b": 0.3, "c": 0.1}, 7)
    assert counts == {"a": 4, "b": 2, "c": 1}


def test_deal_keeps_twins_apart():
    pos = [(float(x), 0.0, 0.0) for x in range(6)]
    faces = crowd.deal(pos, {"a": 3, "b": 3}, random.Random(1))
    assert all(f for f in faces)
    assert all(faces[i] != faces[i + 1] for i in range(5))


def test_assign_is_seeded_and_spaced():
    reg, world = canteen(), floor_world()
    cr = Direction.model_validate(good_direction()).scenes[0].crowd[0].model_copy(update={"do": "stand"})
    cells = reg.areas[0].cells()
    a1 = crowd.assign(cr, reg, world, cells)
    a2 = crowd.assign(cr, reg, world, cells)
    assert [e.pos for e in a1] == [e.pos for e in a2]
    assert 0 < len(a1) <= crowd.CAP
    for i, e in enumerate(a1):
        for f in a1[i + 1:]:
            assert math.dist(e.pos, f.pos) >= crowd.SPACING - 1e-9


def test_queue_faces_the_head_of_the_line():
    reg, world = canteen(), floor_world()
    cr = Direction.model_validate({"scenes": [{"place": "Буфет", "crowd": [
        {"in": "зал", "density": 1, "mix": {"Практиканты": 1}, "do": "queue", "along": "очередь"}]}]}).scenes[0].crowd[0]
    q = crowd.assign(cr, reg, world, reg.areas[0].cells(), line=reg.lines[0], toward=(7.5, 1, 7.5))
    assert len(q) >= 4
    # the line runs from z=6.5 (head) towards z=1.5; everyone looks towards +z
    assert all(abs(e.yaw) < 1 for e in q)


def test_talk_groups_face_their_middle():
    reg, world = canteen(), floor_world()
    cr = Direction.model_validate(good_direction()).scenes[0].crowd[0]
    g = crowd.assign(cr, reg, world, reg.areas[0].cells())
    by = {}
    for e in g:
        by.setdefault(e.group, []).append(e)
    assert by and all(len(m) >= 2 for m in by.values())


# --------------------------------------------------------------------------
# voxels and the camera
# --------------------------------------------------------------------------


def test_traverse_walks_every_cell():
    cells = list(traverse((0.5, 0.5, 0.5), (3.5, 0.5, 0.5)))
    assert cells == [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]


def test_glass_blocks_bodies_but_not_sight():
    w = floor_world()
    w.set((5, 1, 5), solid=True, opaque=False)
    assert not w.standable((5, 1, 5))
    assert w.sees((5.5, 1.5, 3.5), (5.5, 1.5, 7.5))
    assert not w.sees((5.5, 1.5, 3.5), (5.5, 1.5, 7.5), through_glass=False)


def standing(pos, yaw=0.0, seconds=2.0):
    return camera.Track([camera.Body(pos, yaw)] * int(seconds * 20 + 1))


def test_camera_holds_the_ordered_view_when_nothing_is_in_the_way():
    w = floor_world()
    tr = standing((5.5, 1, 5.5), yaw=0)
    p = camera.ride(tr, "a", camera.Scene3D(w), duration=1.0, side="front", distance=(3, 3))
    assert not p.issues
    eye = tr.at(0).eyes
    for s in p.samples:
        assert math.dist(s.pos, eye) == pytest.approx(3.0, abs=0.05)
        assert s.pos[2] > eye[2]  # in front: yaw 0 faces +Z


def test_camera_finds_a_way_past_a_wall():
    w = floor_world()
    for x in range(0, 12):
        for y in range(1, 5):
            w.set((x, y, 7))  # a wall right where the ordered camera would stand
    w.set((5, 1, 7), solid=False)  # …no gap at eye height
    tr = standing((5.5, 1, 5.5), yaw=0)
    sc = camera.Scene3D(w)
    p = camera.ride(tr, "a", sc, duration=1.0, side="front", distance=(3, 3))
    assert not p.issues
    eye = tr.at(0).eyes
    for s in p.samples:
        assert w.free_at(s.pos)
        assert sc.clear(s.pos, eye, s.t)


def test_another_body_in_the_way_is_flown_around():
    w = floor_world()
    tr = standing((5.5, 1, 5.5), yaw=0)
    blocker = standing((5.5, 1, 7.0))
    sc = camera.Scene3D(w, others={"b": blocker})
    p = camera.ride(tr, "a", sc, duration=0.5, side="front", distance=(3.5, 3.5))
    eye = tr.at(0).eyes
    assert all(sc.clear(s.pos, eye, s.t, ("a",)) for s in p.samples)


def test_walled_in_target_is_reported():
    w = floor_world()
    for x in range(4, 7):
        for y in range(1, 5):
            for z in range(4, 7):
                if (x, z) != (5, 5):
                    w.set((x, y, z))
    w.set((5, 3, 5))
    tr = standing((5.5, 1, 5.5))
    p = camera.ride(tr, "a", camera.Scene3D(w), duration=0.5, distance=(3, 3))
    assert p.issues and "walled in" in p.issues[0].what


def test_fixed_camera_reports_rather_than_moves():
    w = floor_world()
    for y in range(1, 5):
        w.set((5, y, 3))
    tr = standing((5.5, 1, 1.5))
    p = camera.fixed((5.5, 2.6, 8.5), camera.Scene3D(w), duration=1.0, aim=tr, aim_name="a")
    assert {s.pos for s in p.samples} == {(5.5, 2.6, 8.5)}
    assert p.issues and "hidden" in p.issues[0].what
