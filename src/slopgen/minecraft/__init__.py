"""Minecraft machinima: a fandom video's picture staged and filmed in the world's own build.

The operator builds the world in Minecraft and marks it up in game (the `slopcam` mod,
`minecraft/slopcam/`): places, and in them zones, points, lines and cameras, each with
a name, a description and roles. A machinima BLOCK is a kind of frame on the picture
track — a run of consecutive stretches of narration, played in that world and filmed
under the very words being said. Its direction file (scenes → cast, crowd, actions,
shots, every key anchored to a word) is written first by a model and then by the
operator, and is always kept, so a block can be edited and recompiled.

Nothing here talks to a model or to the game except `direct` and `client`; the rest
is plain data and geometry, which is what lets it be tested without either.
"""
