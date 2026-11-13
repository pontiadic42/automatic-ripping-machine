/*jshint esversion: 6 */
/*global $:false, addToast:false, activeTab:false */
/*
 * Home page: drive bays, items that need attention, processing jobs, recently finished and system stats.
 * Active jobs come from /json?mode=joblist (polled every index_refresh ms),
 * drives / recent jobs / stats from /json?mode=dashboard (every 15 s).
 */
(function () {
    "use strict";

    const DASHBOARD_MS = 15000;
    const TRANSCODE_STATES = ["transcoding", "waiting_transcode"];
    const PALETTE = [["#CECBF6", "#3C3489"], ["#9FE1CB", "#085041"], ["#F5C4B3", "#712B13"], ["#B5D4F4", "#0C447C"],
        ["#FAC775", "#633806"], ["#F4C0D1", "#72243E"], ["#C0DD97", "#27500A"], ["#D3D1C7", "#444441"]];

    let jobs = [];
    let dashboard = null;
    const samples = {};   // job_id -> [{t, p}] for the client-side ETA
    let modalHref = "";

    function esc(value) {
        return String(value === undefined || value === null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function blank(value) {
        return value === undefined || value === null || value === "" || value === "None" || value === "N/A";
    }

    function hashIndex(text) {
        let h = 0;
        for (let i = 0; i < text.length; i++) {
            h = (h * 31 + text.charCodeAt(i)) % 9973;
        }
        return h % PALETTE.length;
    }

    /** TMDb serves posters at several sizes; "original" is multi-megabyte, so ask for a small one */
    function posterUrl(url, size) {
        if (blank(url)) {
            return "";
        }
        return String(url).replace("/t/p/original/", "/t/p/" + size + "/");
    }

    function posterHtml(url, title, size) {
        const src = posterUrl(url, size);
        const name = blank(title) ? "Unknown disc" : String(title);
        const colors = PALETTE[hashIndex(name)];
        const initials = `<span class="arm-initials" style="color:${colors[1]}">${esc(name)}</span>`;
        if (!src) {
            return `<div class="arm-poster" style="background:${colors[0]}">${initials}</div>`;
        }
        return `<div class="arm-poster" style="background:${colors[0]}">` +
            `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">${initials}</div>`;
    }

    function jobTitle(job) {
        const title = blank(job.title) || String(job.title).toLowerCase() === "not identified" ? "" : job.title;
        if (title) {
            return title + (blank(job.year) || job.year === "0000" ? "" : ` (${job.year})`);
        }
        return blank(job.label) ? "Unlabelled disc" : String(job.label).replace(/_/g, " ");
    }

    function driveName(drive) {
        const dev = blank(drive.mount) ? drive.name : String(drive.mount).replace("/dev/", "");
        const kind = String(drive.type || "").indexOf("BluRay") >= 0 ? "Blu-ray" :
            (String(drive.type || "").indexOf("DVD") >= 0 ? "DVD" : "CD");
        return `${dev} · ${kind}${blank(drive.model) ? "" : " · " + String(drive.model).trim()}`;
    }

    /** 0 identify, 1 rip, 2 transcode, 3 finish */
    function phaseOf(job) {
        if (job.status === "ripping") {
            return 1;
        }
        if (TRANSCODE_STATES.indexOf(job.status) >= 0) {
            return 2;
        }
        if (job.status === "success") {
            return 3;
        }
        return 0;
    }

    function progressOf(job) {
        const value = parseFloat(job.progress);
        return isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
    }

    function stepsHtml(job) {
        const phase = phaseOf(job);
        const pct = progressOf(job);
        const names = ["Identify", job.disctype === "music" ? "Rip audio" : "Rip", "Transcode", "Finish"];
        if (job.disctype === "music" || job.disctype === "data") {
            names.splice(2, 1);
        }
        return names.map(function (name, i) {
            const step = (job.disctype === "music" || job.disctype === "data") && i >= 2 ? i + 1 : i;
            if (step < phase) {
                return `<span class="arm-pill done">${name}</span>`;
            }
            if (step === phase) {
                const waiting = job.status === "waiting";
                const label = waiting ? "Waiting for you" : (pct !== null && step > 0 ? `${name} ${Math.round(pct)}%` : name);
                return `<span class="arm-pill ${waiting ? "warn" : "now"}">${label}</span>`;
            }
            return `<span class="arm-pill">${name}</span>`;
        }).join("");
    }

    /** "00h09m12s" (from ARM) or our own estimate from the last few polls */
    function etaText(job) {
        const pct = progressOf(job);
        if (!blank(job.eta) && job.eta !== "Unknown") {
            const m = String(job.eta).match(/(\d+)h(\d+)m/);
            if (m) {
                const mins = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
                return mins < 1 ? "Almost done" : `About ${mins} min left`;
            }
        }
        const list = samples[job.job_id] || [];
        if (pct === null || list.length < 2) {
            return "";
        }
        const first = list[0];
        const last = list[list.length - 1];
        const rate = (last.p - first.p) / ((last.t - first.t) / 60000);   // percent per minute
        if (!(rate > 0)) {
            return "";
        }
        const mins = Math.round((100 - last.p) / rate);
        return mins < 1 ? "Almost done" : `About ${mins} min left`;
    }

    function trackText(job) {
        const m = String(job.stage || "").match(/Track (\d+)\/(\d+)/);
        return m ? `title ${m[1]} of ${m[2]}` : "";
    }

    function recordSample(job) {
        const pct = progressOf(job);
        if (pct === null) {
            return;
        }
        const list = samples[job.job_id] = samples[job.job_id] || [];
        const last = list[list.length - 1];
        if (last && (pct < last.p || last.phase !== job.status)) {
            list.length = 0;   // new title or new phase: restart the estimate
        }
        list.push({t: Date.now(), p: pct, phase: job.status});
        while (list.length > 2 && list[list.length - 1].t - list[0].t > 180000) {
            list.shift();
        }
    }

    function actionsHtml(job) {
        if (!window.ARM_AUTHENTICATED) {
            return "";
        }
        const id = encodeURIComponent(job.job_id);
        let html = `<a class="arm-btn" href="titlesearch?job_id=${id}">Title search</a>` +
            `<a class="arm-btn" href="customTitle?job_id=${id}">Custom title</a>`;
        if (phaseOf(job) <= 1) {
            html += `<a class="arm-btn" href="changeparams?config_id=${id}">Rip settings</a>`;
        }
        html += `<a class="arm-btn" href="logs?logfile=${encodeURIComponent(job.logfile)}&mode=full">Log</a>` +
            `<button type="button" class="arm-btn arm-btn-danger" data-toggle="modal" data-target="#exampleModal" ` +
            `data-href="json?job=${id}&mode=abandon">Abandon</button>`;
        return html;
    }

    function bayHtml(drive, job) {
        if (!job) {
            return `<div class="arm-card arm-bay empty">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/></svg>
                <div><div style="font-size:14px">${esc(driveName(drive))}</div>
                <div class="arm-faint arm-empty-hint" style="font-size:12px">Empty · insert a disc to start</div></div></div>`;
        }
        const pct = progressOf(job);
        const showBar = pct !== null && phaseOf(job) > 0;
        const meta = [etaText(job), trackText(job)].filter(Boolean).join(" · ");
        const type = blank(job.video_type) || job.video_type === "unknown" ? (job.disctype || "") : job.video_type;
        return `<div class="arm-card arm-bay">
            <a href="/jobdetail?job_id=${encodeURIComponent(job.job_id)}">${posterHtml(job.poster_url, jobTitle(job), "w185")}</a>
            <div class="arm-bay-body">
                <p class="arm-bay-drive">${esc(driveName(drive))}${type ? " · " + esc(type) : ""}</p>
                <p class="arm-bay-title"><a href="/jobdetail?job_id=${encodeURIComponent(job.job_id)}">${esc(jobTitle(job))}</a></p>
                <div class="arm-steps">${stepsHtml(job)}</div>
                ${showBar ? `<div class="arm-bar"><i style="width:${pct.toFixed(1)}%"></i></div>` : ""}
                ${meta ? `<p class="arm-bay-meta">${esc(meta)}</p>` : ""}
                <div class="arm-actions">${actionsHtml(job)}</div>
            </div></div>`;
    }

    function rowHtml(job) {
        const pct = progressOf(job);
        const meta = [etaText(job)].filter(Boolean).join("");
        const label = job.status === "waiting_transcode" ? "Waiting to transcode" : "Transcoding";
        return `<div class="arm-row">
            <span class="arm-dot now"></span>
            <div class="arm-row-main">
                <p class="arm-row-title"><a href="/jobdetail?job_id=${encodeURIComponent(job.job_id)}">${esc(jobTitle(job))}</a></p>
                ${pct !== null ? `<div class="arm-bar"><i style="width:${pct.toFixed(1)}%"></i></div>` : ""}
            </div>
            <span class="arm-row-side">${esc(label)}${pct !== null ? " " + Math.round(pct) + "%" : ""}${meta ? " · " + esc(meta) : ""}</span>
        </div>`;
    }

    function attentionHtml(activeJobs, recent) {
        let html = "";
        activeJobs.filter(function (job) { return job.status === "waiting"; }).forEach(function (job) {
            const id = encodeURIComponent(job.job_id);
            html += `<div class="arm-attn">
                <span>${esc(jobTitle(job))} couldn't be identified. ARM will rip it as-is unless you set a title.</span>
                <span class="arm-attn-actions"><a href="titlesearch?job_id=${id}">Search title</a>
                <a href="customTitle?job_id=${id}">Custom title</a></span></div>`;
        });
        const dayAgo = Date.now() - 24 * 3600 * 1000;
        (recent || []).filter(function (job) {
            return job.status === "fail" && job.stop_time && Date.parse(job.stop_time) > dayAgo;
        }).forEach(function (job) {
            html += `<div class="arm-attn bad">
                <span>${esc(jobTitle(job))} failed${blank(job.errors) ? "" : ": " + esc(String(job.errors).slice(0, 140))}</span>
                <span class="arm-attn-actions"><a href="/jobdetail?job_id=${encodeURIComponent(job.job_id)}">Details</a>
                <a href="logs?logfile=${encodeURIComponent(job.logfile)}&mode=full">Log</a></span></div>`;
        });
        return html;
    }

    function timeAgo(iso) {
        const t = Date.parse(iso);
        if (!t) {
            return "";
        }
        const mins = Math.round((Date.now() - t) / 60000);
        if (mins < 60) {
            return mins <= 1 ? "Just now" : `${mins} min ago`;
        }
        const hours = Math.round(mins / 60);
        if (hours < 24) {
            return `${hours} h ago`;
        }
        const days = Math.round(hours / 24);
        return days === 1 ? "Yesterday" : `${days} days ago`;
    }

    function recentHtml(recent) {
        if (!recent || !recent.length) {
            return `<div class="arm-empty">Finished rips show up here.</div>`;
        }
        return recent.map(function (job) {
            let sub = `<p class="arm-tile-sub">${esc(timeAgo(job.stop_time))}</p>`;
            if (job.status === "fail") {
                sub = `<p class="arm-tile-sub bad">Failed · ${esc(timeAgo(job.stop_time))}</p>`;
            } else if (!job.identified) {
                sub = `<p class="arm-tile-sub warn">Unidentified · ${esc(timeAgo(job.stop_time))}</p>`;
            }
            return `<a class="arm-tile" href="/jobdetail?job_id=${encodeURIComponent(job.job_id)}">
                ${posterHtml(job.poster_url, jobTitle(job), "w185")}
                <div style="min-width:0"><p class="arm-tile-title">${esc(jobTitle(job))}</p>${sub}</div></a>`;
        }).join("");
    }

    function statsHtml(stats) {
        if (!stats) {
            return "";
        }
        const hw = [];
        if (stats.hw && stats.hw.intel) { hw.push("QuickSync"); }
        if (stats.hw && stats.hw.nvidia) { hw.push("NVENC"); }
        if (stats.hw && stats.hw.amd) { hw.push("AMD VCN"); }
        return [
            `CPU <b>${Math.round(stats.cpu)}%</b>`,
            stats.temp ? `<b>${Math.round(stats.temp)}°C</b>` : "",
            `Memory <b>${Math.round(stats.memory_percent)}%</b>`,
            `Media <b>${esc(stats.completed_free)} GB free</b>`,
            `Transcoding <b>${hw.length ? hw.join(", ") : "software"}</b>`,
            `ARM <b>${esc(stats.version)}</b>`
        ].filter(Boolean).map(function (s) { return `<span>${s}</span>`; }).join("");
    }

    function render() {
        const drives = dashboard ? dashboard.drives : [];
        const used = {};
        let bays = "";
        drives.forEach(function (drive) {
            const candidates = jobs.filter(function (job) {
                return job.devpath === drive.mount && TRANSCODE_STATES.indexOf(job.status) < 0;
            });
            const job = candidates.length ? candidates[candidates.length - 1] : null;
            if (job) {
                used[job.job_id] = true;
            }
            bays += bayHtml(drive, job);
        });
        // Jobs that aren't on a known drive (e.g. drive table not filled yet) still get a bay
        jobs.forEach(function (job) {
            if (!used[job.job_id] && TRANSCODE_STATES.indexOf(job.status) < 0) {
                used[job.job_id] = true;
                bays += bayHtml({name: job.devpath, mount: job.devpath, type: job.disctype}, job);
            }
        });
        if (!bays) {
            bays = `<div class="arm-card arm-empty">No drives found yet.</div>`;
        }
        $("#armBays").html(bays);

        const processing = jobs.filter(function (job) { return !used[job.job_id]; });
        $("#armProcessing").html(processing.map(rowHtml).join("")).toggle(processing.length > 0);
        $("#armAttention").html(attentionHtml(jobs, dashboard ? dashboard.recent : []));
        if (dashboard) {
            $("#armRecent").html(recentHtml(dashboard.recent));
            $("#armStats").html(statsHtml(dashboard.stats));
        }
        $("#armUpdated").text(jobs.length ? `${jobs.length} active job${jobs.length > 1 ? "s" : ""}` : "Idle");
    }

    function checkNotes(data) {
        $.each(data.notes || [], function (_i, note) {
            if (!$(`#toast${note.id}`).length && typeof addToast === "function") {
                addToast(note.title, note.message, note.id);
            }
        });
    }

    function refreshJobs() {
        $.ajax({url: "/json?mode=joblist", dataType: "json", timeout: 10000}).done(function (data) {
            jobs = $.map(data.results || {}, function (job) { return job; });
            jobs.sort(function (a, b) { return parseInt(a.job_id, 10) - parseInt(b.job_id, 10); });
            jobs.forEach(recordSample);
            checkNotes(data);
            render();
        }).fail(function () {
            $("#armUpdated").text("Can't reach ARM, retrying…");
        });
    }

    function refreshDashboard() {
        $.ajax({url: "/json?mode=dashboard", dataType: "json", timeout: 20000}).done(function (data) {
            if (data && data.drives) {
                dashboard = data;
                render();
            }
        });
    }

    $(function () {
        if (typeof activeTab === "function") {
            activeTab("home");
        }
        $("#exampleModal").on("show.bs.modal", function (event) {
            modalHref = $(event.relatedTarget).data("href") || "";
        });
        $("#save-yes").on("click", function () {
            if (!modalHref) {
                return;
            }
            $.getJSON(modalHref, function (data) {
                $("#exampleModal").modal("hide");
                $("#message1 .alert-heading").text(data.success ? "Job abandoned" : (data.Error || "Couldn't abandon the job"));
                $("#message1").toggleClass("alert-success", !!data.success).toggleClass("alert-danger", !data.success)
                    .removeClass("d-none");
                setTimeout(function () { $("#message1").addClass("d-none"); }, 5000);
                refreshJobs();
            });
        });
        refreshJobs();
        refreshDashboard();
        setInterval(refreshJobs, Math.max(1000, window.ARM_REFRESH_MS || 2000));
        setInterval(refreshDashboard, DASHBOARD_MS);
    });
})();
