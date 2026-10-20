// SPDX-License-Identifier: LGPL-2.1-or-later
#ifndef MP_WAYLAND_MENU_H
#define MP_WAYLAND_MENU_H
struct vo_wayland_state;
struct mpv_node;
void vo_wayland_menu_init(struct vo_wayland_state *wl);
void vo_wayland_menu_update(struct vo_wayland_state *wl, struct mpv_node *data);
void vo_wayland_menu_uninit(struct vo_wayland_state *wl);
#endif
