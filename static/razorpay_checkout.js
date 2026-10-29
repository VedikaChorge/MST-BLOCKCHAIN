(function () {
  const button = document.getElementById("razorpay-pay");
  if (!button) return;
  const form = document.getElementById("razorpay-form");
  const status = document.getElementById("razorpay-status");
  const d = button.dataset;

  button.addEventListener("click", () => {
    if (typeof window.Razorpay !== "function") {
      status.textContent = "Razorpay checkout could not be loaded. Check your internet connection and reload the page.";
      return;
    }
    const checkout = new window.Razorpay({
      key: d.key,
      order_id: d.orderId,
      amount: d.amount,
      name: "IP Evidence Hub",
      description: d.description,
      handler: (response) => {
        status.textContent = "Payment received. Verifying…";
        form.elements.razorpay_order_id.value = response.razorpay_order_id;
        form.elements.razorpay_payment_id.value = response.razorpay_payment_id;
        form.elements.razorpay_signature.value = response.razorpay_signature;
        form.submit();
      },
      modal: { ondismiss: () => { status.textContent = "Payment window closed. You can try again."; } },
    });
    checkout.on("payment.failed", (response) => {
      status.textContent = "Payment failed: " + (response.error?.description || "unknown error") + ". You can try again.";
    });
    checkout.open();
  });
})();
