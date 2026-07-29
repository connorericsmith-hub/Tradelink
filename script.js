document.getElementById("year").textContent = new Date().getFullYear();

const form = document.querySelector(".estimate-form");
const status = document.getElementById("form-status");

if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    status.textContent = "Sending...";

    const data = new FormData(form);

    fetch("/", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(data).toString(),
    })
      .then(() => {
        status.textContent = "Thanks! We'll be in touch shortly to confirm your free estimate.";
        form.reset();
      })
      .catch(() => {
        status.textContent = "Something went wrong. Please call us at (760) 216-0246.";
      });
  });
}
