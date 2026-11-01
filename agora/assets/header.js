(function () {
  try {
    const P = window.parent;
    const D = P.document;

    const previo = P.__agoraHeaderCleanup;
    if (typeof previo === "function") {
      try { previo(); } catch (e) {}
    }

    let disposed = false;
    let retry = null;
    let intervalo = null;
    let header = null;

    function scrollTop() {
      let maximo = 0;
      const candidatos = [
        P,
        D.scrollingElement,
        D.documentElement,
        D.body,
        D.querySelector('[data-testid="stMain"]'),
        D.querySelector('[data-testid="stAppViewContainer"]'),
        D.querySelector('section.main')
      ];
      for (const elemento of candidatos) {
        if (!elemento) continue;
        const valor = Number(elemento.scrollTop || 0);
        if (valor > maximo) maximo = valor;
      }
      if (typeof P.scrollY === "number" && P.scrollY > maximo) {
        maximo = P.scrollY;
      }
      return maximo;
    }

    function pintar() {
      if (disposed || !header) return;
      const compacto = scrollTop() > 40;
      if (header.classList.contains("is-compact") !== compacto) {
        header.classList.toggle("is-compact", compacto);
      }
    }

    function programar() {
      try {
        P.requestAnimationFrame(pintar);
      } catch (e) {
        pintar();
      }
    }

    function iniciar() {
      if (disposed) return;
      header = D.getElementById("agoraHeader");
      if (!header) {
        retry = P.setTimeout(iniciar, 120);
        return;
      }
      if (retry) {
        P.clearTimeout(retry);
        retry = null;
      }

      const alDesplazar = function () { programar(); };
      D.addEventListener("scroll", alDesplazar, true);
      P.addEventListener("scroll", alDesplazar, { passive: true });
      P.addEventListener("resize", alDesplazar, { passive: true });
      intervalo = P.setInterval(programar, 180);

      P.__agoraHeaderCleanup = function () {
        disposed = true;
        if (retry) P.clearTimeout(retry);
        if (intervalo) P.clearInterval(intervalo);
        D.removeEventListener("scroll", alDesplazar, true);
        P.removeEventListener("scroll", alDesplazar);
        P.removeEventListener("resize", alDesplazar);
        P.__agoraHeaderCleanup = null;
      };

      programar();
    }

    iniciar();
  } catch (error) {
    try { console.warn("Ágora header:", error); } catch (e) {}
  }
})();
