/* Registers the service worker and swaps in a new build when one takes over.
 *
 * The worker URL rides on `data-sw`, injected by web/build.py with the same
 * depth-correct prefix as this <script> tag, so nothing here assumes the site
 * is served from /logbook/ (local `make serve` has no prefix at all).
 */

const tag = document.querySelector("script[data-sw]");

if (tag && "serviceWorker" in navigator) {
  const hadController = Boolean(navigator.serviceWorker.controller);
  let reloading = false;

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    /* A first install also fires this; only reload when replacing a build. */
    if (!hadController || reloading) return;
    reloading = true;
    globalThis.location.reload();
  });

  const activateWaiting = (registration) => {
    if (registration.waiting && navigator.serviceWorker.controller) {
      registration.waiting.postMessage("skip-waiting");
    }
  };

  globalThis.addEventListener("load", async () => {
    try {
      const registration = await navigator.serviceWorker.register(
        tag.dataset.sw,
      );
      activateWaiting(registration);
      registration.addEventListener("updatefound", () => {
        const installing = registration.installing;
        if (!installing) return;
        installing.addEventListener("statechange", () => {
          if (installing.state === "installed") activateWaiting(registration);
        });
      });
    } catch {
      /* Offline first visit, private relay, or no SW support: pages still work. */
    }
  });
}
