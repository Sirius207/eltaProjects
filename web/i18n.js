// Language switch shared by the landing page and the tool.
// Static text carries its Chinese in data-zh (innerHTML) and data-zh-placeholder /
// data-zh-aria-label / data-zh-title. Script text uses HB.t(en, zh).
(() => {
  const KEY = "hb-lang";
  const ATTRS = ["placeholder", "aria-label", "title"];
  const HB = (window.HB = window.HB || {});

  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* storage blocked */ }
  HB.lang = saved || ((navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en");
  HB.t = (en, zh) => (HB.lang === "zh" ? zh : en);

  HB.applyLang = (lang) => {
    HB.lang = lang;
    document.documentElement.lang = lang === "zh" ? "zh-Hant-TW" : "en";
    document.querySelectorAll("[data-zh]").forEach((el) => {
      if (el.dataset.en === undefined) el.dataset.en = el.innerHTML;
      el.innerHTML = lang === "zh" ? el.dataset.zh : el.dataset.en;
    });
    ATTRS.forEach((a) => document.querySelectorAll(`[data-zh-${a}]`).forEach((el) => {
      const key = "en" + a.replace(/-./g, (m) => m[1].toUpperCase()).replace(/^./, (c) => c.toUpperCase());
      if (el.dataset[key] === undefined) el.dataset[key] = el.getAttribute(a) || "";
      el.setAttribute(a, lang === "zh" ? el.getAttribute(`data-zh-${a}`) : el.dataset[key]);
    }));
    document.querySelectorAll("[data-set-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.setLang === lang)));
    try { localStorage.setItem(KEY, lang); } catch (e) { /* storage blocked */ }
    window.dispatchEvent(new CustomEvent("hb-langchange", { detail: lang }));
  };

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-set-lang]");
    if (b) HB.applyLang(b.dataset.setLang);
  });
  const start = () => HB.applyLang(HB.lang);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
