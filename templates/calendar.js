// Calendrier interactif (vues Jour / Semaine / Mois, façon iPhone).
// Tout se joue côté client : on charge les événements en JSON selon la période
// affichée, et un clic sur un jour ouvre une popup pour consulter/ajouter/modifier.
(function () {
    "use strict";

    var DAY_NAMES = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"];
    var DAY_NAMES_SHORT = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
    var MONTH_NAMES = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
        "Août", "Septembre", "Octobre", "Novembre", "Décembre"];

    function parseYMD(s) {
        var parts = s.split("-");
        return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    }
    function fmtYMD(d) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, "0");
        var day = String(d.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + day;
    }
    function addDays(d, n) {
        var r = new Date(d);
        r.setDate(r.getDate() + n);
        return r;
    }
    function startOfWeek(d) {
        // Semaine démarrant le lundi.
        var r = new Date(d);
        var dow = r.getDay(); // 0 = dimanche
        var diff = (dow === 0) ? -6 : 1 - dow;
        r.setDate(r.getDate() + diff);
        r.setHours(0, 0, 0, 0);
        return r;
    }
    function isSameDay(a, b) {
        return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }
    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function fmtDateLong(d) {
        return DAY_NAMES[d.getDay()] + " " + d.getDate() + " " + MONTH_NAMES[d.getMonth()].toLowerCase() + " " + d.getFullYear();
    }

    var today = parseYMD(window.CAL_TODAY);
    var state = {
        view: "month",
        refDate: new Date(today),
        events: [],
    };

    var els = {
        title: document.getElementById("calTitle"),
        prevBtn: document.getElementById("calPrevBtn"),
        nextBtn: document.getElementById("calNextBtn"),
        todayBtn: document.getElementById("calTodayBtn"),
        viewBtns: document.querySelectorAll(".cal-view-btn"),
        monthView: document.getElementById("calMonthView"),
        monthGrid: document.getElementById("calMonthGrid"),
        weekView: document.getElementById("calWeekView"),
        weekGrid: document.getElementById("calWeekGrid"),
        dayView: document.getElementById("calDayView"),
        dayList: document.getElementById("calDayList"),
        modalRoot: document.getElementById("calModalRoot"),
    };

    function currentRange() {
        if (state.view === "day") {
            return { start: state.refDate, end: state.refDate };
        }
        if (state.view === "week") {
            var wstart = startOfWeek(state.refDate);
            return { start: wstart, end: addDays(wstart, 6) };
        }
        // month : on affiche la grille complète (semaines pleines), donc on charge
        // aussi les jours des mois voisins visibles dans la grille.
        var first = new Date(state.refDate.getFullYear(), state.refDate.getMonth(), 1);
        var last = new Date(state.refDate.getFullYear(), state.refDate.getMonth() + 1, 0);
        return { start: startOfWeek(first), end: addDays(startOfWeek(last), 6) };
    }

    function eventsForDay(dateStr) {
        return state.events.filter(function (ev) { return ev.event_date === dateStr; })
            .sort(function (a, b) { return (a.event_time || "99:99").localeCompare(b.event_time || "99:99"); });
    }

    function loadAndRender() {
        var range = currentRange();
        var start = fmtYMD(range.start);
        var end = fmtYMD(range.end);
        fetch("/calendrier/api/events?start=" + start + "&end=" + end)
            .then(function (res) { return res.json(); })
            .then(function (data) {
                state.events = data.events || [];
                render();
            })
            .catch(function () {
                state.events = [];
                render();
            });
    }

    function updateToolbar() {
        els.viewBtns.forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.view === state.view);
        });
        if (state.view === "day") {
            els.title.textContent = fmtDateLong(state.refDate);
        } else if (state.view === "week") {
            var wstart = startOfWeek(state.refDate);
            var wend = addDays(wstart, 6);
            if (wstart.getMonth() === wend.getMonth()) {
                els.title.textContent = wstart.getDate() + " – " + wend.getDate() + " " + MONTH_NAMES[wstart.getMonth()] + " " + wstart.getFullYear();
            } else {
                els.title.textContent = wstart.getDate() + " " + MONTH_NAMES[wstart.getMonth()] + " – " + wend.getDate() + " " + MONTH_NAMES[wend.getMonth()] + " " + wend.getFullYear();
            }
        } else {
            els.title.textContent = MONTH_NAMES[state.refDate.getMonth()] + " " + state.refDate.getFullYear();
        }
    }

    function render() {
        updateToolbar();
        els.monthView.style.display = state.view === "month" ? "" : "none";
        els.weekView.style.display = state.view === "week" ? "" : "none";
        els.dayView.style.display = state.view === "day" ? "" : "none";
        if (state.view === "month") renderMonth();
        else if (state.view === "week") renderWeek();
        else renderDay();
    }

    function renderMonth() {
        var first = new Date(state.refDate.getFullYear(), state.refDate.getMonth(), 1);
        var last = new Date(state.refDate.getFullYear(), state.refDate.getMonth() + 1, 0);
        var gridStart = startOfWeek(first);
        var gridEnd = addDays(startOfWeek(last), 6);
        var html = "";
        var cursor = new Date(gridStart);
        while (cursor <= gridEnd) {
            var dateStr = fmtYMD(cursor);
            var dayEvents = eventsForDay(dateStr);
            var outside = cursor.getMonth() !== state.refDate.getMonth();
            var isToday = isSameDay(cursor, today);
            var classes = "cal-day-cell" + (outside ? " cal-day-outside" : "") + (isToday ? " cal-day-today" : "");
            var chips = dayEvents.slice(0, 3).map(function (ev) {
                return '<div class="cal-event-chip">' + (ev.event_time ? escapeHtml(ev.event_time) + " " : "") + escapeHtml(ev.title) + '</div>';
            }).join("");
            var more = dayEvents.length > 3 ? '<div class="cal-event-chip-more">+' + (dayEvents.length - 3) + '</div>' : "";
            html += '<div class="' + classes + '" data-date="' + dateStr + '">' +
                '<div class="cal-day-number">' + cursor.getDate() + '</div>' +
                '<div class="cal-day-events" data-count="' + (dayEvents.length ? dayEvents.length + ' évt.' : "") + '">' + chips + more + '</div>' +
                '</div>';
            cursor = addDays(cursor, 1);
        }
        els.monthGrid.innerHTML = html;
        Array.prototype.forEach.call(els.monthGrid.querySelectorAll(".cal-day-cell"), function (cell) {
            cell.addEventListener("click", function () { openDayModal(cell.dataset.date); });
        });
    }

    function renderWeek() {
        var wstart = startOfWeek(state.refDate);
        var html = "";
        for (var i = 0; i < 7; i++) {
            var d = addDays(wstart, i);
            var dateStr = fmtYMD(d);
            var dayEvents = eventsForDay(dateStr);
            var isToday = isSameDay(d, today);
            var evHtml = dayEvents.map(function (ev) {
                return '<div class="cal-week-event">' +
                    (ev.event_time ? '<span class="cal-week-event-time">' + escapeHtml(ev.event_time) + '</span> ' : '') +
                    escapeHtml(ev.title) + '</div>';
            }).join("");
            html += '<div class="cal-week-col' + (isToday ? " cal-day-today" : "") + '" data-date="' + dateStr + '">' +
                '<div class="cal-week-col-header">' +
                '<div class="cal-week-day-name">' + DAY_NAMES_SHORT[d.getDay()] + '</div>' +
                '<div class="cal-day-number">' + d.getDate() + '</div>' +
                '</div>' +
                '<div class="cal-week-col-body">' + evHtml + '</div>' +
                '</div>';
        }
        els.weekGrid.innerHTML = html;
        Array.prototype.forEach.call(els.weekGrid.querySelectorAll(".cal-week-col"), function (col) {
            col.addEventListener("click", function () { openDayModal(col.dataset.date); });
        });
    }

    function renderDay() {
        var dateStr = fmtYMD(state.refDate);
        var dayEvents = eventsForDay(dateStr);
        var html = "";
        if (!dayEvents.length) {
            html += '<p class="cal-modal-empty">Aucun événement ce jour-là.</p>';
        } else {
            dayEvents.forEach(function (ev) {
                html += '<div class="cal-day-view-event" data-date="' + dateStr + '">' +
                    '<div class="cal-day-view-time">' + (ev.event_time ? escapeHtml(ev.event_time) : "—") + '</div>' +
                    '<div class="cal-day-view-body">' +
                    '<div class="cal-event-title">' + escapeHtml(ev.title) + '</div>' +
                    (ev.description ? '<div class="cal-event-desc">' + escapeHtml(ev.description) + '</div>' : '') +
                    '<div class="cal-event-meta">Ajouté par ' + escapeHtml(ev.created_by) + '</div>' +
                    '</div></div>';
            });
        }
        html += '<div class="cal-day-view-add" data-date="' + dateStr + '">＋ Ajouter un événement ce jour-là</div>';
        els.dayList.innerHTML = html;
        Array.prototype.forEach.call(els.dayList.querySelectorAll("[data-date]"), function (node) {
            node.addEventListener("click", function () { openDayModal(node.dataset.date); });
        });
    }

    // --- Popup jour (consultation + ajout + édition + suppression) ---
    function closeModal() {
        els.modalRoot.innerHTML = "";
        document.removeEventListener("keydown", onModalKeydown);
    }
    function onModalKeydown(e) {
        if (e.key === "Escape") closeModal();
    }

    function openDayModal(dateStr) {
        var d = parseYMD(dateStr);
        renderModal(dateStr, fmtDateLong(d));
        document.addEventListener("keydown", onModalKeydown);
    }

    function renderModal(dateStr, titleStr) {
        var dayEvents = eventsForDay(dateStr);
        var listHtml = "";
        if (!dayEvents.length) {
            listHtml = '<p class="cal-modal-empty">Aucun événement ce jour-là pour l\'instant.</p>';
        } else {
            listHtml = '<div class="cal-modal-event-list">' + dayEvents.map(function (ev) {
                return '<div class="cal-modal-event-item" data-id="' + ev.id + '">' +
                    '<div class="cal-event-time">' + (ev.event_time ? escapeHtml(ev.event_time) : "Toute la journée") + '</div>' +
                    '<div class="cal-event-title">' + escapeHtml(ev.title) + '</div>' +
                    (ev.description ? '<div class="cal-event-desc">' + escapeHtml(ev.description) + '</div>' : '') +
                    '<div class="cal-event-meta">Ajouté par ' + escapeHtml(ev.created_by) + '</div>' +
                    (ev.can_edit ? (
                        '<div class="cal-modal-event-actions">' +
                        '<button type="button" class="docs-mini-btn cal-edit-btn" data-id="' + ev.id + '">✏️ Modifier</button>' +
                        '<button type="button" class="docs-mini-btn docs-mini-danger cal-delete-btn" data-id="' + ev.id + '">🗑️ Supprimer</button>' +
                        '</div>'
                    ) : '') +
                    '</div>';
            }).join("") + '</div>';
        }
        els.modalRoot.innerHTML =
            '<div class="cal-modal-backdrop" id="calModalBackdrop">' +
            '<div class="cal-modal">' +
            '<div class="cal-modal-header">' +
            '<span class="cal-modal-date-title">' + escapeHtml(titleStr) + '</span>' +
            '<button type="button" class="cal-modal-close" id="calModalClose">✕</button>' +
            '</div>' +
            '<div id="calModalBody">' + listHtml + '</div>' +
            '<div class="cal-modal-add">' +
            '<div class="cal-modal-add-title">Ajouter un événement</div>' +
            '<form class="docs-form" id="calAddForm">' +
            '<div class="form-group"><label>Titre</label><input type="text" name="title" placeholder="ex. Réunion staff" required></div>' +
            '<div class="cal-form-row">' +
            '<div class="form-group"><label>Heure (optionnel)</label><input type="time" name="event_time"></div>' +
            '</div>' +
            '<div class="form-group"><label>Détails (optionnel)</label><input type="text" name="description" placeholder="Lieu, précisions..."></div>' +
            '<button type="submit" class="btn">Ajouter</button>' +
            '<p class="cal-modal-error" id="calAddError" style="display:none;color:var(--negative);font-size:12px;"></p>' +
            '</form>' +
            '</div>' +
            '</div></div>';

        document.getElementById("calModalBackdrop").addEventListener("click", function (e) {
            if (e.target.id === "calModalBackdrop") closeModal();
        });
        document.getElementById("calModalClose").addEventListener("click", closeModal);

        Array.prototype.forEach.call(els.modalRoot.querySelectorAll(".cal-delete-btn"), function (btn) {
            btn.addEventListener("click", function () {
                if (!confirm("Supprimer cet événement ?")) return;
                fetch("/calendrier/api/" + btn.dataset.id + "/supprimer", { method: "POST" })
                    .then(function (res) { return res.json(); })
                    .then(function (data) {
                        if (data.error) { alert(data.error); return; }
                        state.events = state.events.filter(function (ev) { return String(ev.id) !== String(btn.dataset.id); });
                        renderModal(dateStr, titleStr);
                        render();
                    });
            });
        });

        Array.prototype.forEach.call(els.modalRoot.querySelectorAll(".cal-edit-btn"), function (btn) {
            btn.addEventListener("click", function () {
                var ev = dayEvents.filter(function (e2) { return String(e2.id) === String(btn.dataset.id); })[0];
                if (!ev) return;
                showEditForm(ev, dateStr, titleStr);
            });
        });

        document.getElementById("calAddForm").addEventListener("submit", function (e) {
            e.preventDefault();
            var form = e.target;
            var payload = {
                title: form.title.value,
                event_date: dateStr,
                event_time: form.event_time.value,
                description: form.description.value,
            };
            fetch("/calendrier/api/ajouter", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            })
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    var errEl = document.getElementById("calAddError");
                    if (data.error) {
                        errEl.textContent = data.error;
                        errEl.style.display = "";
                        return;
                    }
                    state.events.push(data.event);
                    renderModal(dateStr, titleStr);
                    render();
                });
        });
    }

    function showEditForm(ev, dateStr, titleStr) {
        var item = els.modalRoot.querySelector('.cal-modal-event-item[data-id="' + ev.id + '"]');
        if (!item) return;
        item.innerHTML =
            '<form class="cal-modal-edit-form" data-id="' + ev.id + '">' +
            '<div class="form-group"><label>Titre</label><input type="text" name="title" value="' + escapeHtml(ev.title) + '" required></div>' +
            '<div class="cal-form-row">' +
            '<div class="form-group"><label>Date</label><input type="date" name="event_date" value="' + ev.event_date + '" required></div>' +
            '<div class="form-group"><label>Heure</label><input type="time" name="event_time" value="' + escapeHtml(ev.event_time || "") + '"></div>' +
            '</div>' +
            '<div class="form-group"><label>Détails</label><input type="text" name="description" value="' + escapeHtml(ev.description || "") + '"></div>' +
            '<div class="cal-modal-event-actions">' +
            '<button type="submit" class="btn btn-secondary">Enregistrer</button>' +
            '<button type="button" class="docs-mini-btn cal-cancel-edit">Annuler</button>' +
            '</div>' +
            '<p class="cal-modal-error" style="display:none;color:var(--negative);font-size:12px;"></p>' +
            '</form>';
        item.querySelector(".cal-cancel-edit").addEventListener("click", function () {
            renderModal(dateStr, titleStr);
        });
        item.querySelector("form").addEventListener("submit", function (e) {
            e.preventDefault();
            var form = e.target;
            var payload = {
                title: form.title.value,
                event_date: form.event_date.value,
                event_time: form.event_time.value,
                description: form.description.value,
            };
            fetch("/calendrier/api/" + ev.id + "/modifier", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            })
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    var errEl = form.querySelector(".cal-modal-error");
                    if (data.error) {
                        errEl.textContent = data.error;
                        errEl.style.display = "";
                        return;
                    }
                    state.events = state.events.map(function (e2) { return String(e2.id) === String(ev.id) ? data.event : e2; });
                    var movedDay = data.event.event_date !== dateStr;
                    if (movedDay) {
                        loadAndRender();
                        closeModal();
                    } else {
                        renderModal(dateStr, titleStr);
                        render();
                    }
                });
        });
    }

    // --- Navigation / toolbar ---
    els.prevBtn.addEventListener("click", function () {
        if (state.view === "day") state.refDate = addDays(state.refDate, -1);
        else if (state.view === "week") state.refDate = addDays(state.refDate, -7);
        else state.refDate = new Date(state.refDate.getFullYear(), state.refDate.getMonth() - 1, 1);
        loadAndRender();
    });
    els.nextBtn.addEventListener("click", function () {
        if (state.view === "day") state.refDate = addDays(state.refDate, 1);
        else if (state.view === "week") state.refDate = addDays(state.refDate, 7);
        else state.refDate = new Date(state.refDate.getFullYear(), state.refDate.getMonth() + 1, 1);
        loadAndRender();
    });
    els.todayBtn.addEventListener("click", function () {
        state.refDate = new Date(today);
        loadAndRender();
    });
    Array.prototype.forEach.call(els.viewBtns, function (btn) {
        btn.addEventListener("click", function () {
            state.view = btn.dataset.view;
            loadAndRender();
        });
    });

    // Vue par défaut : Mois, positionné sur aujourd'hui.
    state.view = "month";
    loadAndRender();
})();
