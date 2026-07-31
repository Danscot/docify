from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    # Auth
    path("signup/",       views.signup,      name="signup"),
    path("login/",        views.user_login,  name="login"),
    path("logout/",       views.user_logout, name="logout"),

    # Profile
    path("profile/",          views.profile,         name="profile"),
    path("profile/password/", views.change_password, name="change_password"),

    # Workspaces
    path("workspaces/",                              views.workspace_list,    name="workspace_list"),
    path("workspaces/create/",                       views.workspace_create,  name="workspace_create"),
    path("workspaces/<uuid:pk>/switch/",             views.workspace_switch,  name="workspace_switch"),
    path("workspaces/<uuid:pk>/settings/",           views.workspace_settings,name="workspace_settings"),
    path("workspaces/<uuid:pk>/members/<int:user_id>/remove/",      views.member_remove,      name="member_remove"),
    path("workspaces/<uuid:pk>/members/<int:user_id>/role/",        views.member_role_change, name="member_role_change"),

    # Invitations
    path("workspaces/<uuid:pk>/invite/",             views.invite_send,        name="invite_send"),
    path("workspaces/<uuid:pk>/invite/<uuid:token>/cancel/", views.invite_cancel, name="invite_cancel"),
    path("invite/accept/<uuid:token>/",              views.invite_accept,      name="invite_accept"),
    path("invitations/",                             views.my_invitations,     name="my_invitations"),
    path("invitations/<uuid:token>/respond/",        views.invitation_respond, name="invitation_respond"),
    path("users/lookup/",                            views.user_lookup,        name="user_lookup"),
]
