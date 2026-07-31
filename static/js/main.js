document.addEventListener("DOMContentLoaded", () => {

  // Auto-dismiss flash messages after 4s
  document.querySelectorAll(".message").forEach(el => {
    setTimeout(() => {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 400);
    }, 4000);
  });

  // Mobile sidebar drawer
  const hamburger = document.getElementById("hamburger");
  const sidebar   = document.querySelector(".sidebar");
  const overlay   = document.getElementById("sidebar-overlay");

  function openSidebar() {
    sidebar.classList.add("open");
    overlay.classList.add("open");
    document.body.style.overflow = "hidden";
  }
  function closeSidebar() {
    sidebar.classList.remove("open");
    overlay.classList.remove("open");
    document.body.style.overflow = "";
  }

  if (hamburger) {
    hamburger.addEventListener("click", () => {
      sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
    });
  }
  if (overlay) {
    overlay.addEventListener("click", closeSidebar);
  }

  // Close sidebar on nav link tap (mobile)
  document.querySelectorAll(".nav-link").forEach(link => {
    link.addEventListener("click", () => {
      if (window.innerWidth <= 768) closeSidebar();
    });
  });

});

// ── Workspace switcher ──────────────────────────────────────────────────────
(function() {
  var switcher   = document.getElementById('ws-switcher');
  var switcherBtn = document.getElementById('ws-switcher-btn');
  var dropdown   = document.getElementById('ws-dropdown');

  if (switcherBtn) {
    switcherBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      switcher.classList.toggle('open');
      userMenu.classList.remove('open');
    });
  }

  // ── User menu ─────────────────────────────────────────────────────────────
  var userMenu   = document.getElementById('sidebar-user') || { classList: { remove: function(){} } };
  var userBtn    = document.getElementById('user-menu-btn');
  var userDrop   = document.getElementById('user-dropdown');

  if (userBtn) {
    userBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      userMenu.classList.toggle('open');
      if (switcher) switcher.classList.remove('open');
    });
  }

  // Close both on outside click
  document.addEventListener('click', function() {
    if (switcher) switcher.classList.remove('open');
    if (userMenu) userMenu.classList.remove('open');
  });
})();
