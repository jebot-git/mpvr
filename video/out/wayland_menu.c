// SPDX-License-Identifier: LGPL-2.1-or-later
// Export mpv's live menu-data on the session bus, associated with this wl_surface.
#include <gio/gio.h>
#include <limits.h>
#include <string.h>

#include "common/msg.h"
#include "input/input.h"
#include "mpv/client.h"
#include "wayland_common.h"
#include "wayland_menu.h"
#include "appmenu.h"

#define MENU_PATH "/org/mpvr/Menu"
#define MENU_IFACE "com.canonical.dbusmenu"

struct item {
    int id;
    bool enabled;
    char *command;
    GVariant *props;
    GPtrArray *children;
};

struct wayland_menu {
    GDBusConnection *bus;
    GDBusNodeInfo *info;
    GMainContext *context;
    GMainLoop *loop;
    GThread *thread;
    GMutex lock;
    guint registration;
    guint revision;
    int next_id;
    struct item *root;
    struct input_ctx *input;
    struct org_kde_kwin_appmenu *surface_menu;
};

static const char interface_xml[] =
    "<node><interface name='" MENU_IFACE "'>"
    "<property name='Version' type='u' access='read'/>"
    "<property name='TextDirection' type='s' access='read'/>"
    "<property name='Status' type='s' access='read'/>"
    "<property name='IconThemePath' type='as' access='read'/>"
    "<method name='GetLayout'><arg type='i' direction='in'/>"
    "<arg type='i' direction='in'/><arg type='as' direction='in'/>"
    "<arg type='u' direction='out'/><arg type='(ia{sv}av)' direction='out'/></method>"
    "<method name='GetGroupProperties'><arg type='ai' direction='in'/>"
    "<arg type='as' direction='in'/><arg type='a(ia{sv})' direction='out'/></method>"
    "<method name='GetProperty'><arg type='i' direction='in'/>"
    "<arg type='s' direction='in'/><arg type='v' direction='out'/></method>"
    "<method name='Event'><arg type='i' direction='in'/><arg type='s' direction='in'/>"
    "<arg type='v' direction='in'/><arg type='u' direction='in'/></method>"
    "<method name='EventGroup'><arg type='a(isvu)' direction='in'/>"
    "<arg type='ai' direction='out'/></method>"
    "<method name='AboutToShow'><arg type='i' direction='in'/>"
    "<arg type='b' direction='out'/></method>"
    "<method name='AboutToShowGroup'><arg type='ai' direction='in'/>"
    "<arg type='ai' direction='out'/><arg type='ai' direction='out'/></method>"
    "<signal name='LayoutUpdated'><arg type='u'/><arg type='i'/></signal>"
    "<signal name='ItemsPropertiesUpdated'><arg type='a(ia{sv})'/>"
    "<arg type='a(ias)'/></signal>"
    "<signal name='ItemActivationRequested'><arg type='i'/><arg type='u'/></signal>"
    "</interface></node>";

static void free_item(gpointer ptr)
{
    struct item *item = ptr;
    g_free(item->command);
    g_variant_unref(item->props);
    g_ptr_array_unref(item->children);
    g_free(item);
}

static struct item *new_item(int id)
{
    struct item *item = g_new0(struct item, 1);
    item->id = id;
    item->enabled = true;
    item->children = g_ptr_array_new_with_free_func(free_item);
    return item;
}

static mpv_node *field(mpv_node *node, const char *key)
{
    if (node->format == MPV_FORMAT_NODE_MAP) {
        for (int i = 0; i < node->u.list->num; i++) {
            if (!strcmp(node->u.list->keys[i], key))
                return &node->u.list->values[i];
        }
    }
    return NULL;
}

static const char *string_field(mpv_node *node, const char *key)
{
    mpv_node *value = field(node, key);
    return value && value->format == MPV_FORMAT_STRING ? value->u.string : "";
}

static bool has_state(mpv_node *node, const char *state)
{
    mpv_node *value = field(node, "state");
    if (value && value->format == MPV_FORMAT_NODE_ARRAY) {
        for (int i = 0; i < value->u.list->num; i++) {
            mpv_node *s = &value->u.list->values[i];
            if (s->format == MPV_FORMAT_STRING && !strcmp(s->u.string, state))
                return true;
        }
    }
    return false;
}

// mpv uses ampersands for mnemonics; DBusMenu uses underscores.
static char *menu_label(const char *title)
{
    GString *label = g_string_new(NULL);
    for (const char *p = title; *p; p++) {
        if (*p == '&') {
            if (p[1] == '&') {
                g_string_append_c(label, '&');
                p++;
            } else if (p[1]) {
                g_string_append_c(label, '_');
            }
        } else {
            if (*p == '_')
                g_string_append_c(label, '_');
            g_string_append_c(label, *p);
        }
    }
    return g_string_free(label, FALSE);
}

static void build_items(struct wayland_menu *menu, struct item *parent,
                        mpv_node *data, int depth)
{
    if (!data || data->format != MPV_FORMAT_NODE_ARRAY || depth > 32)
        return;
    for (int i = 0; i < data->u.list->num && menu->next_id < INT_MAX - 1; i++) {
        mpv_node *node = &data->u.list->values[i];
        if (node->format != MPV_FORMAT_NODE_MAP)
            continue;
        bool separator = !strcmp(string_field(node, "type"), "separator");
        bool submenu = !strcmp(string_field(node, "type"), "submenu");
        const char *title = string_field(node, "title");
        if (!separator && !title[0])
            continue;
        struct item *item = new_item(++menu->next_id);
        item->command = g_strdup(string_field(node, "cmd"));
        item->enabled = parent->enabled &&
            !has_state(node, "disabled") && !has_state(node, "hidden") &&
            (submenu || (item->command[0] && item->command[0] != '#' &&
                         strcmp(item->command, "ignore")));
        GVariantBuilder props;
        g_variant_builder_init(&props, G_VARIANT_TYPE_VARDICT);
        g_variant_builder_add(&props, "{sv}", "enabled", g_variant_new_boolean(item->enabled));
        g_variant_builder_add(&props, "{sv}", "visible", g_variant_new_boolean(!has_state(node, "hidden")));
        if (separator) {
            g_variant_builder_add(&props, "{sv}", "type", g_variant_new_string("separator"));
        } else {
            char *label = menu_label(title);
            g_variant_builder_add(&props, "{sv}", "label", g_variant_new_string(label));
            g_free(label);
        }
        if (has_state(node, "checked")) {
            g_variant_builder_add(&props, "{sv}", "toggle-type", g_variant_new_string("checkmark"));
            g_variant_builder_add(&props, "{sv}", "toggle-state", g_variant_new_int32(1));
        }
        if (submenu) {
            g_variant_builder_add(&props, "{sv}", "children-display", g_variant_new_string("submenu"));
            build_items(menu, item, field(node, "submenu"), depth + 1);
        }
        const char *shortcut = string_field(node, "shortcut");
        if (shortcut[0]) {
            // DBusMenu shortcuts are arrays of key sequences, e.g. [[Control, p]].
            char **keys = g_strsplit(shortcut, "+", -1);
            for (int k = 0; keys[k]; k++) {
                if (!g_ascii_strcasecmp(keys[k], "ctrl")) {
                    g_free(keys[k]);
                    keys[k] = g_strdup("Control");
                }
            }
            GVariant *sequence = g_variant_new_strv((const char *const *)keys, -1);
            g_variant_builder_add(&props, "{sv}", "shortcut",
                                  g_variant_new_array(G_VARIANT_TYPE_STRING_ARRAY, &sequence, 1));
            g_strfreev(keys);
        }
        item->props = g_variant_ref_sink(g_variant_builder_end(&props));
        g_ptr_array_add(parent->children, item);
    }
}

static struct item *find_item(struct item *item, int id)
{
    if (item->id == id)
        return item;
    for (guint i = 0; i < item->children->len; i++) {
        struct item *found = find_item(g_ptr_array_index(item->children, i), id);
        if (found)
            return found;
    }
    return NULL;
}

static GVariant *properties(struct item *item, const char **names)
{
    if (!names[0])
        return g_variant_ref(item->props);
    GVariantBuilder props;
    g_variant_builder_init(&props, G_VARIANT_TYPE_VARDICT);
    for (int i = 0; names[i]; i++) {
        GVariant *value = g_variant_lookup_value(item->props, names[i], NULL);
        if (value) {
            g_variant_builder_add(&props, "{sv}", names[i], value);
            g_variant_unref(value);
        }
    }
    return g_variant_builder_end(&props);
}

static GVariant *layout(struct item *item, int depth, const char **names)
{
    GVariantBuilder children;
    g_variant_builder_init(&children, G_VARIANT_TYPE("av"));
    if (depth != 0) {
        for (guint i = 0; i < item->children->len; i++)
            g_variant_builder_add(&children, "v", layout(g_ptr_array_index(item->children, i),
                                                       depth < 0 ? -1 : depth - 1, names));
    }
    return g_variant_new("(i@a{sv}av)", item->id, properties(item, names), &children);
}

static bool dispatch_event(struct wayland_menu *menu, GVariant *event)
{
    int id;
    const char *name;
    GVariant *data;
    guint timestamp;
    g_variant_get(event, "(i&s@vu)", &id, &name, &data, &timestamp);
    struct item *item = find_item(menu->root, id);
    if (item && item->enabled && item->command && item->command[0] &&
        !strcmp(name, "clicked") && !item->children->len) {
        mp_cmd_t *cmd = mp_input_parse_cmd(menu->input, bstr0(item->command), "<appmenu>");
        if (cmd)
            mp_input_queue_cmd(menu->input, cmd);
    }
    g_variant_unref(data);
    return item != NULL;
}

static void method_call(GDBusConnection *connection, const char *sender,
                        const char *path, const char *interface, const char *method,
                        GVariant *params, GDBusMethodInvocation *invocation, gpointer ctx)
{
    struct wayland_menu *menu = ctx;
    GVariant *result = NULL;
    g_mutex_lock(&menu->lock);
    if (!strcmp(method, "GetLayout")) {
        int id, depth;
        const char **names;
        g_variant_get(params, "(ii^a&s)", &id, &depth, &names);
        struct item *item = find_item(menu->root, id);
        if (item)
            result = g_variant_new("(u@(ia{sv}av))", menu->revision, layout(item, depth, names));
        g_free(names);
    } else if (!strcmp(method, "GetGroupProperties")) {
        GVariantIter *ids;
        const char **names;
        g_variant_get(params, "(ai^a&s)", &ids, &names);
        GVariantBuilder props;
        g_variant_builder_init(&props, G_VARIANT_TYPE("a(ia{sv})"));
        int id;
        while (g_variant_iter_next(ids, "i", &id)) {
            struct item *item = find_item(menu->root, id);
            if (item)
                g_variant_builder_add(&props, "(i@a{sv})", id, properties(item, names));
        }
        result = g_variant_new("(a(ia{sv}))", &props);
        g_variant_iter_free(ids);
        g_free(names);
    } else if (!strcmp(method, "GetProperty")) {
        int id;
        const char *name;
        g_variant_get(params, "(i&s)", &id, &name);
        struct item *item = find_item(menu->root, id);
        GVariant *value = item ? g_variant_lookup_value(item->props, name, NULL) : NULL;
        if (value) {
            result = g_variant_new("(v)", value);
            g_variant_unref(value);
        }
    } else if (!strcmp(method, "Event")) {
        if (dispatch_event(menu, params))
            result = g_variant_new("()");
    } else if (!strcmp(method, "EventGroup")) {
        GVariantIter *events;
        g_variant_get(params, "(a(isvu))", &events);
        GVariantBuilder errors;
        g_variant_builder_init(&errors, G_VARIANT_TYPE("ai"));
        GVariant *event;
        while ((event = g_variant_iter_next_value(events))) {
            if (!dispatch_event(menu, event)) {
                int id;
                g_variant_get_child(event, 0, "i", &id);
                g_variant_builder_add(&errors, "i", id);
            }
            g_variant_unref(event);
        }
        result = g_variant_new("(ai)", &errors);
        g_variant_iter_free(events);
    } else if (!strcmp(method, "AboutToShow")) {
        int id;
        g_variant_get(params, "(i)", &id);
        if (find_item(menu->root, id))
            result = g_variant_new("(b)", FALSE);
    } else if (!strcmp(method, "AboutToShowGroup")) {
        GVariantIter *ids;
        g_variant_get(params, "(ai)", &ids);
        GVariantBuilder updates, errors;
        g_variant_builder_init(&updates, G_VARIANT_TYPE("ai"));
        g_variant_builder_init(&errors, G_VARIANT_TYPE("ai"));
        int id;
        while (g_variant_iter_next(ids, "i", &id)) {
            if (!find_item(menu->root, id))
                g_variant_builder_add(&errors, "i", id);
        }
        result = g_variant_new("(aiai)", &updates, &errors);
        g_variant_iter_free(ids);
    }
    g_mutex_unlock(&menu->lock);
    if (result)
        g_dbus_method_invocation_return_value(invocation, result);
    else
        g_dbus_method_invocation_return_dbus_error(invocation,
            MENU_IFACE ".Error.InvalidMenuItem", "Unknown menu item or property");
}

static GVariant *get_property(GDBusConnection *connection, const char *sender,
                             const char *path, const char *interface, const char *name,
                             GError **error, gpointer ctx)
{
    if (!strcmp(name, "Version"))
        return g_variant_new_uint32(3);
    if (!strcmp(name, "TextDirection"))
        return g_variant_new_string("ltr");
    if (!strcmp(name, "Status"))
        return g_variant_new_string("normal");
    if (!strcmp(name, "IconThemePath"))
        return g_variant_new_strv(NULL, 0);
    return NULL;
}

static const GDBusInterfaceVTable vtable = {
    .method_call = method_call,
    .get_property = get_property,
};

static gpointer run_menu(gpointer ptr)
{
    struct wayland_menu *menu = ptr;
    g_main_context_push_thread_default(menu->context);
    g_main_loop_run(menu->loop);
    g_main_context_pop_thread_default(menu->context);
    return NULL;
}

static gboolean quit_menu(gpointer ptr)
{
    g_main_loop_quit(ptr);
    return G_SOURCE_REMOVE;
}

void vo_wayland_menu_init(struct vo_wayland_state *wl)
{
    if (!wl->appmenu_manager)
        return;
    struct wayland_menu *menu = g_new0(struct wayland_menu, 1);
    wl->menu = menu;
    g_mutex_init(&menu->lock);
    menu->input = wl->vo->input_ctx;
    menu->root = new_item(0);
    menu->root->props = g_variant_ref_sink(g_variant_new_array(G_VARIANT_TYPE("{sv}"), NULL, 0));
    menu->context = g_main_context_new();
    menu->loop = g_main_loop_new(menu->context, FALSE);
    GError *error = NULL;
    char *address = g_dbus_address_get_for_bus_sync(G_BUS_TYPE_SESSION, NULL, &error);
    if (address) {
        menu->bus = g_dbus_connection_new_for_address_sync(address,
            G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT |
            G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION, NULL, NULL, &error);
        g_free(address);
    }
    if (menu->bus) {
        g_dbus_connection_set_exit_on_close(menu->bus, FALSE);
        menu->info = g_dbus_node_info_new_for_xml(interface_xml, NULL);
        g_main_context_push_thread_default(menu->context);
        menu->registration = g_dbus_connection_register_object(menu->bus, MENU_PATH,
            menu->info->interfaces[0], &vtable, menu, NULL, &error);
        g_main_context_pop_thread_default(menu->context);
    }
    if (!menu->registration) {
        MP_VERBOSE(wl, "AppMenu unavailable: %s\n", error ? error->message : "no session bus");
        g_clear_error(&error);
        vo_wayland_menu_uninit(wl);
        return;
    }
    menu->thread = g_thread_new("mpvr-appmenu", run_menu, menu);
    menu->surface_menu = org_kde_kwin_appmenu_manager_create(wl->appmenu_manager, wl->surface);
    org_kde_kwin_appmenu_set_address(menu->surface_menu,
        g_dbus_connection_get_unique_name(menu->bus), MENU_PATH);
}

void vo_wayland_menu_update(struct vo_wayland_state *wl, mpv_node *data)
{
    struct wayland_menu *menu = wl->menu;
    if (!menu)
        return;
    g_mutex_lock(&menu->lock);
    g_ptr_array_set_size(menu->root->children, 0);
    build_items(menu, menu->root, data, 0);
    // Context-menu actions need a header in a menu bar. Keep native submenus
    // at the top level, and group the remaining actions under Player.
    struct item *player = new_item(menu->next_id < INT_MAX ? ++menu->next_id : INT_MAX);
    player->props = g_variant_ref_sink(g_variant_new_parsed(
        "{'label': <'_Player'>, 'children-display': <'submenu'>}"));
    player->enabled = true;
    for (guint i = 0; i < menu->root->children->len;) {
        struct item *item = g_ptr_array_index(menu->root->children, i);
        GVariant *submenu = g_variant_lookup_value(item->props, "children-display", NULL);
        if (submenu) {
            g_variant_unref(submenu);
            i++;
        } else {
            g_ptr_array_add(player->children, g_ptr_array_steal_index(menu->root->children, i));
        }
    }
    if (player->children->len)
        g_ptr_array_insert(menu->root->children, 0, player);
    else
        free_item(player);
    menu->revision++;
    g_dbus_connection_emit_signal(menu->bus, NULL, MENU_PATH, MENU_IFACE,
        "LayoutUpdated", g_variant_new("(ui)", menu->revision, 0), NULL);
    g_mutex_unlock(&menu->lock);
}

void vo_wayland_menu_uninit(struct vo_wayland_state *wl)
{
    struct wayland_menu *menu = wl->menu;
    if (!menu)
        return;
    if (menu->surface_menu)
        org_kde_kwin_appmenu_release(menu->surface_menu);
    if (menu->thread) {
        // Quit from within the loop, including if teardown raced thread startup.
        GSource *quit = g_idle_source_new();
        g_source_set_callback(quit, quit_menu, menu->loop, NULL);
        g_source_attach(quit, menu->context);
        g_source_unref(quit);
        g_thread_join(menu->thread);
    }
    if (menu->registration)
        g_dbus_connection_unregister_object(menu->bus, menu->registration);
    if (menu->bus) {
        g_dbus_connection_close_sync(menu->bus, NULL, NULL);
        g_object_unref(menu->bus);
    }
    if (menu->info)
        g_dbus_node_info_unref(menu->info);
    g_main_loop_unref(menu->loop);
    g_main_context_unref(menu->context);
    free_item(menu->root);
    g_mutex_clear(&menu->lock);
    g_free(menu);
    wl->menu = NULL;
}
