const horizon = document.querySelector("#time-horizon");
const valueLabel = document.querySelector(".range-value");

if (horizon && valueLabel) {
  function updateHorizon() {
    const value = Number(horizon.value);
    const min = Number(horizon.min);
    const max = Number(horizon.max);
    const progress = ((value - min) / (max - min)) * 100;

    valueLabel.innerHTML = `${value} <span>years</span>`;
    horizon.setAttribute("aria-valuetext", `${value} years`);
    horizon.style.setProperty("--range-progress", `${progress}%`);
  }

  horizon.addEventListener("input", updateHorizon);
  updateHorizon();
}
