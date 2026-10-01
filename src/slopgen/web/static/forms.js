// The generation forms, as data.
//
// They used to be markup, three near-identical blocks of it, and every card added by
// hand had to be put in the right place by hand too — which is how a launch button
// ends up in the wrong form and a column ends up with a hole under it. Here a card
// says what is IN it; `compose` decides where it goes and how wide it is.
//
// A field is `{f: name, kind, l: label key}` plus whatever that kind needs. `["row2",
// a, b]` puts two side by side, `{when: "src=x", rows: [...]}` shows its rows only
// when another field says so, and `{slot: "class"}` leaves an empty box for code that
// fills it in (the filter rows, the breakpoint chips).
//
// `cls: ["w2"]` asks for a double-width card. It is a request, not an instruction:
// `compose` grants it when there are columns to spare and ignores it when there are
// not, which is what keeps one description working from a phone to a wide screen.
const FORMS = {
  "fandom": [
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.world",
      "rows": [
        [
          "row2",
          {
            "f": "fandom",
            "kind": "select",
            "id": "f-world",
            "l": "web.f.which"
          },
          {
            "f": "lang",
            "kind": "select",
            "cls": "f-lang",
            "l": "web.f.lang"
          }
        ],
        {
          "f": "voice",
          "kind": "select",
          "id": "f-voice",
          "l": "web.f.narrator"
        },
        {
          "when": "voice=usher",
          "rows": [
            {
              "f": "viewer_role",
              "kind": "text",
              "rows": 2,
              "ph": "web.a.role",
              "l": "web.f.role"
            },
            {
              "note": "web.role.note",
              "cls": "dim"
            }
          ]
        },
        {
          "f": "scenario",
          "kind": "text",
          "rows": 3,
          "ph": "web.a.brief",
          "l": "web.f.brief"
        },
        {
          "ai": "f-brief-ai",
          "ph": "web.a.aibrief",
          "go": "web.f.writebrief"
        },
        {
          "f": "fandom_invent",
          "kind": "range",
          "min": "0",
          "max": "2",
          "step": "1",
          "value": "0",
          "id": "f-invent",
          "l": "web.f.invent",
          "dose": {
            "v": "",
            "cls": "invent-val"
          }
        },
        {
          "note": "",
          "cls": "dim invent-note"
        },
        {
          "f": "fandom_shapes",
          "kind": "select",
          "id": "f-shapes",
          "l": "web.f.shapes"
        },
        {
          "note": "web.shapes.note",
          "cls": "dim"
        },
        {
          "f": "profanity",
          "kind": "range",
          "min": "0",
          "max": "100",
          "step": "10",
          "value": "0",
          "l": "web.f.swear",
          "dose": {
            "v": "0",
            "cls": "prof-val"
          }
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.source",
      "rows": [
        {
          "f": "medium",
          "kind": "select",
          "id": "f-medium",
          "l": "web.f.medium"
        },
        {
          "f": "source",
          "kind": "select",
          "id": "f-source",
          "l": "web.f.src"
        },
        {
          "note": "",
          "cls": "dim src-note"
        },
        {
          "when": "source=frames",
          "rows": [
            {
              "f": "frame_fit",
              "kind": "select",
              "id": "f-fit",
              "l": "web.f.askwhen"
            },
            {
              "f": "cut_sensitivity",
              "kind": "range",
              "min": "0",
              "max": "1",
              "step": "0.05",
              "value": "0.35",
              "id": "f-sens",
              "l": "web.f.cutrate",
              "dose": {
                "v": "0.35",
                "id": "sens-val"
              }
            },
            {
              "f": "frame_by_hand",
              "kind": "checkbox",
              "inline": true,
              "id": "f-byhand",
              "l": "web.f.byhand"
            },
            {
              "note": "web.byhand.note",
              "cls": "dim"
            },
            {
              "f": "frame_effects",
              "kind": "checkbox",
              "inline": true,
              "checked": true,
              "id": "f-fxauto",
              "l": "web.f.fxauto"
            },
            {
              "note": "web.fxauto.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.amount",
      "rows": [
        [
          "row2",
          {
            "f": "duration_s",
            "kind": "number",
            "min": "10",
            "max": "600",
            "value": "45",
            "l": "web.f.len"
          },
          {
            "f": "count",
            "kind": "number",
            "min": "1",
            "max": "20",
            "value": "1",
            "l": "web.f.count",
            "when1": "loop_on!=*"
          }
        ],
        {
          "f": "title",
          "kind": "text",
          "ph": "web.a.optional",
          "l": "web.f.name"
        }
      ]
    },
    {
      "cls": [
        "w2",
        "fxcard"
      ],
      "title": "web.card.fx",
      "rows": [
        {
          "note": "web.fx.note",
          "cls": "dim"
        },
        {
          "slot": true,
          "class": "fx-rows"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.voice",
      "rows": [
        {
          "f": "tts_engine",
          "kind": "select",
          "cls": "f-tts",
          "l": "web.f.engine"
        },
        {
          "f": "voice_override",
          "kind": "select",
          "cls": "f-voice-pick",
          "l": "web.f.clone"
        },
        {
          "f": "tts_deliveries",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.deliveries"
        },
        {
          "note": "web.deliveries.note",
          "cls": "dim"
        },
        {
          "f": "tts_rate",
          "kind": "range",
          "min": "-50",
          "max": "50",
          "step": "5",
          "value": "0",
          "l": "web.f.rate",
          "dose": {
            "v": "0",
            "cls": "rate-val"
          }
        },
        {
          "f": "music",
          "kind": "select",
          "cls": "f-music",
          "l": "web.f.music"
        },
        {
          "note": "web.music.note"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.picture",
      "rows": [
        {
          "f": "visual_notes",
          "kind": "text",
          "rows": 2,
          "ph": "web.a.limits",
          "l": "web.f.limits"
        },
        {
          "f": "visual_style",
          "kind": "text",
          "rows": 2,
          "ph": "web.a.style",
          "l": "web.f.style"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.subs",
      "rows": [
        {
          "f": "subtitle_style",
          "kind": "select",
          "cls": "f-subs",
          "l": "web.f.style"
        },
        {
          "f": "clean_subtitles",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.clean",
          "when1": "profanity>0"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.ad",
      "rows": [
        {
          "f": "ad",
          "kind": "select",
          "cls": "f-ad",
          "l": "web.f.contract"
        },
        {
          "f": "ad_mode",
          "kind": "select",
          "cls": "f-admode",
          "l": "web.f.mode",
          "when1": "ad=*"
        },
        {
          "when": "ad=manual",
          "rows": [
            {
              "note": "web.admanual.note",
              "cls": "dim"
            },
            {
              "f": "ad_url",
              "kind": "text",
              "ph": "web.f.adurl.ph",
              "l": "web.f.adurl"
            },
            {
              "f": "ov_text",
              "kind": "text",
              "l": "web.f.ovtext"
            },
            {
              "f": "ov_pos",
              "kind": "select",
              "cls": "f-ovpos",
              "l": "web.f.ovpos"
            },
            [
              "row2",
              {
                "f": "ov_start",
                "kind": "number",
                "min": "0",
                "max": "600",
                "step": "0.5",
                "value": "6",
                "l": "web.f.ovstart"
              },
              {
                "f": "ov_dur",
                "kind": "number",
                "min": "1",
                "max": "600",
                "step": "0.5",
                "value": "8",
                "l": "web.f.ovdur"
              }
            ],
            {
              "f": "ad_points",
              "kind": "text",
              "rows": 2,
              "l": "web.f.adpoints"
            }
          ]
        },
        {
          "f": "push",
          "kind": "select",
          "cls": "f-push",
          "l": "web.f.publish",
          "when1": "dry_run!=1"
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.loop",
      "rows": [
        {
          "f": "loop_on",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.loop"
        },
        {
          "when": "loop_on=*",
          "rows": [
            [
              "row2",
              {
                "f": "loop_source",
                "kind": "select",
                "cls": "f-loopsrc",
                "l": "web.f.loopwho"
              },
              {
                "f": "loop_limit",
                "kind": "number",
                "min": "0",
                "step": "1",
                "value": "0",
                "l": "web.f.looplimit"
              }
            ],
            {
              "f": "loop_topics",
              "kind": "text",
              "rows": 3,
              "ph": "web.a.looptopics",
              "l": "web.f.looptopics"
            },
            {
              "f": "loop_ahead",
              "kind": "number",
              "min": "0",
              "max": "50",
              "step": "1",
              "value": "0",
              "l": "web.f.loopahead",
              "when1": "loop_source=ai"
            },
            {
              "f": "loop_park",
              "kind": "select",
              "cls": "f-looppark",
              "l": "web.f.looppark"
            },
            {
              "note": "web.loop.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.bps",
      "rows": [
        {
          "slot": true,
          "class": "chips bps",
          "data-for": "fandom"
        }
      ]
    },
    {
      "cls": [
        "launch"
      ],
      "title": "web.mode.fandom",
      "sub": "web.mode.fandom.sub",
      "go": "web.go",
      "gocls": "primary big-go",
      "go2": {
        "id": "f-by-hand-go",
        "l": "web.f.byhandgo",
        "cls": "ghost big-go"
      },
      "rows": [
        {
          "f": "write_metadata",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.meta"
        },
        {
          "note": "web.f.meta.note",
          "cls": "dim"
        },
        {
          "f": "keep_temp",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.keeptmp"
        },
        {
          "f": "dry_run",
          "kind": "checkbox",
          "checked": true,
          "inline": true,
          "l": "web.f.dry"
        }
      ]
    }
  ],
  "info": [
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.about",
      "rows": [
        [
          "row2",
          {
            "f": "lang",
            "kind": "select",
            "cls": "f-lang",
            "l": "web.f.lang"
          },
          {
            "f": "content_type",
            "kind": "select",
            "id": "i-type",
            "l": "web.f.kind"
          }
        ],
        {
          "f": "idea",
          "kind": "text",
          "rows": 3,
          "ph": "web.a.topic",
          "l": "web.f.idea"
        },
        {
          "ai": "i-topic-ai",
          "ph": "web.a.aitopic",
          "go": "web.f.writetopic"
        },
        {
          "f": "profanity",
          "kind": "range",
          "min": "0",
          "max": "100",
          "step": "10",
          "value": "0",
          "l": "web.f.swear",
          "dose": {
            "v": "0",
            "cls": "prof-val"
          }
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.footage",
      "rows": [
        {
          "f": "visuals",
          "kind": "select",
          "id": "i-visuals",
          "l": "web.f.profile"
        },
        {
          "note": "",
          "cls": "dim vis-note"
        },
        {
          "note": "web.bg.note",
          "cls": "dim sub-head"
        },
        {
          "f": "bg_source",
          "kind": "select",
          "cls": "f-bgsrc",
          "l": "web.f.bgsrc"
        },
        {
          "when": "bg_source=stock_video|stock_photo|ai_video|ai_photo",
          "rows": [
            {
              "f": "bg_manual",
              "kind": "checkbox",
              "inline": true,
              "l": "web.f.byme"
            },
            {
              "note": "web.byme.note",
              "cls": "dim"
            }
          ]
        },
        {
          "when": "bg_source=ai_video",
          "rows": [
            {
              "when": "bg_manual!=*",
              "rows": [
                {
                  "f": "bg_ai_vmodel",
                  "kind": "select",
                  "cls": "f-aivid",
                  "l": "web.f.aimodel"
                }
              ]
            }
          ]
        },
        {
          "when": "bg_source=ai_photo",
          "rows": [
            {
              "when": "bg_manual!=*",
              "rows": [
                {
                  "f": "bg_ai_pmodel",
                  "kind": "select",
                  "cls": "f-aiphoto",
                  "l": "web.f.aimodel"
                }
              ]
            }
          ]
        },
        {
          "f": "bg_link",
          "kind": "select",
          "cls": "f-bglink",
          "l": "web.f.bglink"
        },
        {
          "when": "bg_source=local_video|local_photo",
          "rows": [
            {
              "f": "bg_dir",
              "kind": "text",
              "l": "web.f.bgdir"
            }
          ]
        },
        {
          "when": "bg_source=stock_photo|local_photo|ai_photo",
          "rows": [
            [
              "row2",
              {
                "f": "bg_interval",
                "kind": "number",
                "min": "1",
                "max": "30",
                "step": "0.5",
                "value": "3.5",
                "l": "web.f.bgint"
              },
              {
                "f": "bg_motion",
                "kind": "select",
                "cls": "f-motion",
                "l": "web.f.bgmotion"
              }
            ]
          ]
        },
        {
          "when": "bg_source=stock_video|local_video",
          "rows": [
            {
              "f": "bg_cont",
              "kind": "checkbox",
              "inline": true,
              "l": "web.f.bgcont"
            },
            {
              "note": "web.bgcont.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.inserts",
      "rows": [
        {
          "f": "fg_on",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.fgon"
        },
        {
          "note": "web.fgon.note",
          "cls": "dim"
        },
        {
          "when": "fg_on=*",
          "rows": [
            {
              "f": "fg_source",
              "kind": "select",
              "cls": "f-fgsrc",
              "l": "web.f.fgsrc"
            },
            {
              "when": "fg_source=stock_photo|stock_video|ai_photo|ai_video",
              "rows": [
                {
                  "f": "fg_manual",
                  "kind": "checkbox",
                  "inline": true,
                  "l": "web.f.byme"
                }
              ]
            },
            {
              "when": "fg_source=ai_video",
              "rows": [
                {
                  "when": "fg_manual!=*",
                  "rows": [
                    {
                      "f": "fg_ai_vmodel",
                      "kind": "select",
                      "cls": "f-aivid",
                      "l": "web.f.aimodel"
                    }
                  ]
                }
              ]
            },
            {
              "when": "fg_source=ai_photo",
              "rows": [
                {
                  "when": "fg_manual!=*",
                  "rows": [
                    {
                      "f": "fg_ai_pmodel",
                      "kind": "select",
                      "cls": "f-aiphoto",
                      "l": "web.f.aimodel"
                    }
                  ]
                }
              ]
            },
            [
              "row2",
              {
                "f": "fg_width",
                "kind": "number",
                "min": "10",
                "max": "100",
                "value": "78",
                "l": "web.f.fgwidth"
              },
              {
                "f": "fg_pos",
                "kind": "select",
                "cls": "f-fgpos",
                "l": "web.f.fgpos"
              }
            ]
          ]
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.amount",
      "rows": [
        [
          "row2",
          {
            "f": "duration_s",
            "kind": "number",
            "min": "10",
            "max": "600",
            "value": "45",
            "l": "web.f.len"
          },
          {
            "f": "count",
            "kind": "number",
            "min": "1",
            "max": "20",
            "value": "1",
            "l": "web.f.count",
            "when1": "loop_on!=*"
          }
        ],
        {
          "f": "title",
          "kind": "text",
          "ph": "web.a.optional",
          "l": "web.f.name"
        }
      ]
    },
    {
      "cls": [
        "w2",
        "fxcard"
      ],
      "title": "web.card.fx",
      "rows": [
        {
          "slot": true,
          "class": "fx-rows"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.voice",
      "rows": [
        {
          "f": "tts_engine",
          "kind": "select",
          "cls": "f-tts",
          "l": "web.f.engine"
        },
        {
          "f": "voice_override",
          "kind": "select",
          "cls": "f-voice-pick",
          "l": "web.f.clone"
        },
        {
          "f": "tts_deliveries",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.deliveries"
        },
        {
          "note": "web.deliveries.note",
          "cls": "dim"
        },
        {
          "f": "tts_rate",
          "kind": "range",
          "min": "-50",
          "max": "50",
          "step": "5",
          "value": "0",
          "l": "web.f.rate",
          "dose": {
            "v": "0",
            "cls": "rate-val"
          }
        },
        {
          "f": "music",
          "kind": "select",
          "cls": "f-music",
          "l": "web.f.music"
        },
        {
          "note": "web.music.note"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.picture",
      "rows": [
        {
          "f": "visual_notes",
          "kind": "text",
          "rows": 2,
          "l": "web.f.limits"
        },
        {
          "f": "visual_style",
          "kind": "text",
          "rows": 2,
          "l": "web.f.style"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.subs",
      "rows": [
        {
          "f": "subtitle_style",
          "kind": "select",
          "cls": "f-subs",
          "l": "web.f.style"
        },
        {
          "f": "clean_subtitles",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.clean",
          "when1": "profanity>0"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.ad",
      "rows": [
        {
          "f": "ad",
          "kind": "select",
          "cls": "f-ad",
          "l": "web.f.contract"
        },
        {
          "f": "ad_mode",
          "kind": "select",
          "cls": "f-admode",
          "l": "web.f.mode",
          "when1": "ad=*"
        },
        {
          "when": "ad=manual",
          "rows": [
            {
              "note": "web.admanual.note",
              "cls": "dim"
            },
            {
              "f": "ad_url",
              "kind": "text",
              "ph": "web.f.adurl.ph",
              "l": "web.f.adurl"
            },
            {
              "f": "ov_text",
              "kind": "text",
              "l": "web.f.ovtext"
            },
            {
              "f": "ov_pos",
              "kind": "select",
              "cls": "f-ovpos",
              "l": "web.f.ovpos"
            },
            [
              "row2",
              {
                "f": "ov_start",
                "kind": "number",
                "min": "0",
                "max": "600",
                "step": "0.5",
                "value": "6",
                "l": "web.f.ovstart"
              },
              {
                "f": "ov_dur",
                "kind": "number",
                "min": "1",
                "max": "600",
                "step": "0.5",
                "value": "8",
                "l": "web.f.ovdur"
              }
            ],
            {
              "f": "ad_points",
              "kind": "text",
              "rows": 2,
              "l": "web.f.adpoints"
            }
          ]
        },
        {
          "f": "push",
          "kind": "select",
          "cls": "f-push",
          "l": "web.f.publish",
          "when1": "dry_run!=1"
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.loop",
      "rows": [
        {
          "f": "loop_on",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.loop"
        },
        {
          "when": "loop_on=*",
          "rows": [
            [
              "row2",
              {
                "f": "loop_source",
                "kind": "select",
                "cls": "f-loopsrc",
                "l": "web.f.loopwho"
              },
              {
                "f": "loop_limit",
                "kind": "number",
                "min": "0",
                "step": "1",
                "value": "0",
                "l": "web.f.looplimit"
              }
            ],
            {
              "f": "loop_topics",
              "kind": "text",
              "rows": 3,
              "ph": "web.a.looptopics",
              "l": "web.f.looptopics"
            },
            {
              "f": "loop_ahead",
              "kind": "number",
              "min": "0",
              "max": "50",
              "step": "1",
              "value": "0",
              "l": "web.f.loopahead",
              "when1": "loop_source=ai"
            },
            {
              "f": "loop_park",
              "kind": "select",
              "cls": "f-looppark",
              "l": "web.f.looppark"
            },
            {
              "note": "web.loop.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.bps",
      "rows": [
        {
          "slot": true,
          "class": "chips bps",
          "data-for": "info"
        }
      ]
    },
    {
      "cls": [
        "launch"
      ],
      "title": "web.mode.info",
      "sub": "web.mode.info.sub",
      "go": "web.go",
      "gocls": "primary big-go",
      "rows": [
        {
          "f": "write_metadata",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.meta"
        },
        {
          "note": "web.f.meta.note",
          "cls": "dim"
        },
        {
          "f": "keep_temp",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.keeptmp"
        },
        {
          "f": "dry_run",
          "kind": "checkbox",
          "checked": true,
          "inline": true,
          "l": "web.f.dry"
        }
      ]
    }
  ],
  "drama": [
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.premise",
      "rows": [
        {
          "f": "lang",
          "kind": "select",
          "cls": "f-lang",
          "l": "web.f.lang"
        },
        {
          "f": "scenario",
          "kind": "text",
          "rows": 3,
          "ph": "web.a.blank",
          "l": "web.f.plot"
        },
        {
          "ai": "d-story-ai",
          "ph": "web.a.aistory",
          "go": "web.f.polish"
        },
        {
          "f": "profanity",
          "kind": "range",
          "min": "0",
          "max": "100",
          "step": "10",
          "value": "0",
          "l": "web.f.swear",
          "dose": {
            "v": "0",
            "cls": "prof-val"
          }
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.cast",
      "rows": [
        {
          "slot": true,
          "class": "chips",
          "id": "d-cast"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.gen",
      "rows": [
        {
          "f": "orchestration",
          "kind": "select",
          "id": "d-orch",
          "l": "web.f.chain"
        },
        {
          "f": "clip_seconds",
          "kind": "number",
          "min": "0",
          "max": "30",
          "value": "0",
          "l": "web.f.clip"
        },
        {
          "note": "web.f.clip.note",
          "cls": "dim"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.amount",
      "rows": [
        [
          "row2",
          {
            "f": "duration_s",
            "kind": "number",
            "min": "10",
            "max": "900",
            "value": "45",
            "l": "web.f.len"
          },
          {
            "f": "duration_tol_s",
            "kind": "number",
            "min": "0",
            "max": "120",
            "value": "0",
            "l": "web.f.slack"
          }
        ],
        [
          "row2",
          {
            "f": "parts",
            "kind": "number",
            "min": "1",
            "max": "12",
            "value": "1",
            "l": "web.f.eps"
          },
          {
            "f": "count",
            "kind": "number",
            "min": "1",
            "max": "20",
            "value": "1",
            "l": "web.f.count",
            "when1": "loop_on!=*"
          }
        ],
        {
          "f": "parts_iterative",
          "kind": "checkbox",
          "checked": true,
          "when1": "parts>1",
          "inline": true,
          "l": "web.f.oneby"
        },
        {
          "f": "title",
          "kind": "text",
          "ph": "web.a.optional",
          "l": "web.f.name"
        }
      ]
    },
    {
      "cls": [
        "w2",
        "fxcard"
      ],
      "title": "web.card.fx",
      "rows": [
        {
          "slot": true,
          "class": "fx-rows"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.voice",
      "rows": [
        {
          "f": "tts_engine",
          "kind": "select",
          "cls": "f-tts",
          "l": "web.f.engine"
        },
        {
          "f": "voice_override",
          "kind": "select",
          "cls": "f-voice-pick",
          "l": "web.f.clone"
        },
        {
          "f": "tts_deliveries",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.deliveries"
        },
        {
          "note": "web.deliveries.note",
          "cls": "dim"
        },
        {
          "f": "tts_rate",
          "kind": "range",
          "min": "-50",
          "max": "50",
          "step": "5",
          "value": "0",
          "l": "web.f.rate",
          "dose": {
            "v": "0",
            "cls": "rate-val"
          }
        },
        {
          "f": "music",
          "kind": "select",
          "cls": "f-music",
          "l": "web.f.music"
        },
        {
          "note": "web.music.note"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.picture",
      "rows": [
        {
          "f": "visual_notes",
          "kind": "text",
          "rows": 2,
          "l": "web.f.limits"
        },
        {
          "f": "visual_style",
          "kind": "text",
          "rows": 2,
          "l": "web.f.style"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.subs",
      "rows": [
        {
          "f": "subtitle_style",
          "kind": "select",
          "cls": "f-subs",
          "l": "web.f.style"
        },
        {
          "f": "clean_subtitles",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.clean",
          "when1": "profanity>0"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.ad",
      "rows": [
        {
          "f": "ad",
          "kind": "select",
          "cls": "f-ad",
          "l": "web.f.contract"
        },
        {
          "f": "ad_mode",
          "kind": "select",
          "cls": "f-admode",
          "l": "web.f.mode",
          "when1": "ad=*"
        },
        {
          "when": "ad=manual",
          "rows": [
            {
              "note": "web.admanual.note",
              "cls": "dim"
            },
            {
              "f": "ad_url",
              "kind": "text",
              "ph": "web.f.adurl.ph",
              "l": "web.f.adurl"
            },
            {
              "f": "ov_text",
              "kind": "text",
              "l": "web.f.ovtext"
            },
            {
              "f": "ov_pos",
              "kind": "select",
              "cls": "f-ovpos",
              "l": "web.f.ovpos"
            },
            [
              "row2",
              {
                "f": "ov_start",
                "kind": "number",
                "min": "0",
                "max": "600",
                "step": "0.5",
                "value": "6",
                "l": "web.f.ovstart"
              },
              {
                "f": "ov_dur",
                "kind": "number",
                "min": "1",
                "max": "600",
                "step": "0.5",
                "value": "8",
                "l": "web.f.ovdur"
              }
            ],
            {
              "f": "ad_points",
              "kind": "text",
              "rows": 2,
              "l": "web.f.adpoints"
            }
          ]
        },
        {
          "f": "push",
          "kind": "select",
          "cls": "f-push",
          "l": "web.f.publish",
          "when1": "dry_run!=1"
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.loop",
      "rows": [
        {
          "f": "loop_on",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.loop"
        },
        {
          "when": "loop_on=*",
          "rows": [
            [
              "row2",
              {
                "f": "loop_source",
                "kind": "select",
                "cls": "f-loopsrc",
                "l": "web.f.loopwho"
              },
              {
                "f": "loop_limit",
                "kind": "number",
                "min": "0",
                "step": "1",
                "value": "0",
                "l": "web.f.looplimit"
              }
            ],
            {
              "f": "loop_topics",
              "kind": "text",
              "rows": 3,
              "ph": "web.a.looptopics",
              "l": "web.f.looptopics"
            },
            {
              "f": "loop_ahead",
              "kind": "number",
              "min": "0",
              "max": "50",
              "step": "1",
              "value": "0",
              "l": "web.f.loopahead",
              "when1": "loop_source=ai"
            },
            {
              "f": "loop_park",
              "kind": "select",
              "cls": "f-looppark",
              "l": "web.f.looppark"
            },
            {
              "note": "web.loop.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.bps",
      "rows": [
        {
          "slot": true,
          "class": "chips bps",
          "data-for": "drama"
        }
      ]
    },
    {
      "cls": [
        "launch"
      ],
      "title": "web.mode.drama",
      "sub": "web.mode.drama.sub",
      "go": "web.go",
      "gocls": "primary big-go",
      "rows": [
        {
          "f": "write_metadata",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.meta"
        },
        {
          "note": "web.f.meta.note",
          "cls": "dim"
        },
        {
          "f": "keep_temp",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.keeptmp"
        },
        {
          "f": "dry_run",
          "kind": "checkbox",
          "checked": true,
          "inline": true,
          "l": "web.f.dry"
        }
      ]
    }
  ],
  "chat": [
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.chat",
      "rows": [
        [
          "row2",
          {
            "f": "lang",
            "kind": "select",
            "cls": "f-lang",
            "l": "web.f.lang"
          },
          {
            "f": "chat",
            "kind": "select",
            "cls": "f-chatpreset",
            "l": "web.f.chatpreset"
          }
        ],
        {
          "f": "chat_from",
          "kind": "text",
          "rows": 1,
          "ph": "web.a.chatfrom",
          "l": "web.f.chatfrom"
        },
        {
          "f": "chat_voice",
          "kind": "select",
          "cls": "f-chatvoice",
          "l": "web.f.chatvoice"
        },
        {
          "note": "web.chat.note",
          "cls": "dim"
        },
        {
          "f": "translate",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.chattr",
          "checked": true
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.chatlook",
      "rows": [
        {
          "f": "skin",
          "kind": "select",
          "cls": "f-skin",
          "l": "web.f.skin"
        },
        {
          "f": "header",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.chathead",
          "checked": true
        },
        {
          "when": "header=*",
          "rows": [
            {
              "f": "header_avatar",
              "kind": "select",
              "cls": "f-avatar",
              "l": "web.f.chatavatar"
            }
          ]
        },
        {
          "when": "skin=telegram",
          "rows": [
            {
              "f": "me",
              "kind": "select",
              "cls": "f-persona",
              "l": "web.f.chatme"
            },
            {
              "f": "background",
              "kind": "select",
              "cls": "f-chatbg",
              "l": "web.f.chatbg"
            }
          ]
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.chatclock",
      "rows": [
        {
          "f": "scroll",
          "kind": "select",
          "cls": "f-scroll",
          "l": "web.f.scroll"
        },
        {
          "note": "",
          "cls": "dim scr-note"
        },
        {
          "when": "scroll=roll",
          "rows": [
            {
              "f": "roll_s",
              "kind": "number",
              "min": "0",
              "max": "3",
              "step": "0.05",
              "value": "0.45",
              "l": "web.f.rolls"
            }
          ]
        },
        {
          "f": "gap_s",
          "kind": "number",
          "min": "0",
          "max": "10",
          "step": "0.1",
          "value": "1.2",
          "l": "web.f.gaps"
        },
        [
          "row2",
          {
            "f": "chunk",
            "kind": "number",
            "min": "0",
            "max": "400",
            "step": "10",
            "value": "90",
            "l": "web.f.chunk"
          },
          {
            "f": "chunk_min",
            "kind": "number",
            "min": "0",
            "max": "800",
            "step": "10",
            "value": "140",
            "l": "web.f.chunkmin"
          }
        ],
        {
          "f": "reactions",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.chatreact",
          "checked": true
        },
        {
          "when": "reactions=*",
          "rows": [
            {
              "f": "react_s",
              "kind": "number",
              "min": "0",
              "max": "4",
              "step": "0.1",
              "value": "0.5",
              "l": "web.f.reacts"
            }
          ]
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.amount",
      "rows": [
        {
          "f": "count",
          "kind": "number",
          "min": "1",
          "max": "20",
          "value": "1",
          "l": "web.f.count",
          "when1": "loop_on!=*"
        },
        {
          "note": "web.chat.len.note",
          "cls": "dim"
        },
        {
          "f": "title",
          "kind": "text",
          "ph": "web.a.optional",
          "l": "web.f.name"
        }
      ]
    },
    {
      "cls": [
        "w2",
        "fxcard"
      ],
      "title": "web.card.fx",
      "rows": [
        {
          "slot": true,
          "class": "fx-rows"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.voice",
      "rows": [
        {
          "f": "tts_engine",
          "kind": "select",
          "cls": "f-tts",
          "l": "web.f.engine"
        },
        {
          "f": "voice_override",
          "kind": "select",
          "cls": "f-voice-pick",
          "l": "web.f.clone"
        },
        {
          "f": "tts_deliveries",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.deliveries"
        },
        {
          "note": "web.deliveries.note",
          "cls": "dim"
        },
        {
          "f": "tts_rate",
          "kind": "range",
          "min": "-50",
          "max": "50",
          "step": "5",
          "value": "0",
          "l": "web.f.rate",
          "dose": {
            "v": "0",
            "cls": "rate-val"
          }
        },
        {
          "f": "music",
          "kind": "select",
          "cls": "f-music",
          "l": "web.f.music"
        },
        {
          "note": "web.music.note"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.subs",
      "rows": [
        {
          "f": "subtitle_style",
          "kind": "select",
          "cls": "f-subs",
          "l": "web.f.style"
        },
        {
          "f": "clean_subtitles",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.clean",
          "when1": "profanity>0"
        }
      ]
    },
    {
      "cls": [],
      "title": "web.card.ad",
      "rows": [
        {
          "f": "ad",
          "kind": "select",
          "cls": "f-ad",
          "l": "web.f.contract"
        },
        {
          "f": "ad_mode",
          "kind": "select",
          "cls": "f-admode",
          "l": "web.f.mode",
          "when1": "ad=*"
        },
        {
          "when": "ad=manual",
          "rows": [
            {
              "note": "web.admanual.note",
              "cls": "dim"
            },
            {
              "f": "ad_url",
              "kind": "text",
              "ph": "web.f.adurl.ph",
              "l": "web.f.adurl"
            },
            {
              "f": "ov_text",
              "kind": "text",
              "l": "web.f.ovtext"
            },
            {
              "f": "ov_pos",
              "kind": "select",
              "cls": "f-ovpos",
              "l": "web.f.ovpos"
            },
            [
              "row2",
              {
                "f": "ov_start",
                "kind": "number",
                "min": "0",
                "max": "600",
                "step": "0.5",
                "value": "6",
                "l": "web.f.ovstart"
              },
              {
                "f": "ov_dur",
                "kind": "number",
                "min": "1",
                "max": "600",
                "step": "0.5",
                "value": "8",
                "l": "web.f.ovdur"
              }
            ],
            {
              "f": "ad_points",
              "kind": "text",
              "rows": 2,
              "l": "web.f.adpoints"
            }
          ]
        },
        {
          "f": "push",
          "kind": "select",
          "cls": "f-push",
          "l": "web.f.publish",
          "when1": "dry_run!=1"
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.loop",
      "rows": [
        {
          "f": "loop_on",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.loop"
        },
        {
          "when": "loop_on=*",
          "rows": [
            [
              "row2",
              {
                "f": "loop_source",
                "kind": "select",
                "cls": "f-loopsrc",
                "l": "web.f.loopwho"
              },
              {
                "f": "loop_limit",
                "kind": "number",
                "min": "0",
                "step": "1",
                "value": "0",
                "l": "web.f.looplimit"
              }
            ],
            {
              "f": "loop_topics",
              "kind": "text",
              "rows": 3,
              "ph": "web.a.looptopics",
              "l": "web.f.looptopics"
            },
            {
              "f": "loop_ahead",
              "kind": "number",
              "min": "0",
              "max": "50",
              "step": "1",
              "value": "0",
              "l": "web.f.loopahead",
              "when1": "loop_source=ai"
            },
            {
              "f": "loop_park",
              "kind": "select",
              "cls": "f-looppark",
              "l": "web.f.looppark"
            },
            {
              "note": "web.loop.note",
              "cls": "dim"
            }
          ]
        }
      ]
    },
    {
      "cls": [
        "w2"
      ],
      "title": "web.card.bps",
      "rows": [
        {
          "slot": true,
          "class": "chips bps",
          "data-for": "chat"
        }
      ]
    },
    {
      "cls": [
        "launch"
      ],
      "title": "web.mode.chat",
      "sub": "web.mode.chat.sub",
      "go": "web.go",
      "gocls": "primary big-go",
      "rows": [
        {
          "f": "write_metadata",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.meta"
        },
        {
          "note": "web.f.meta.note",
          "cls": "dim"
        },
        {
          "f": "keep_temp",
          "kind": "checkbox",
          "inline": true,
          "l": "web.f.keeptmp"
        },
        {
          "f": "dry_run",
          "kind": "checkbox",
          "checked": true,
          "inline": true,
          "l": "web.f.dry"
        }
      ]
    }
  ]
};
