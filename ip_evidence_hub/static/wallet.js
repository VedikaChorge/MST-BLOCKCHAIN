(function () {
  const button = document.getElementById("wallet-anchor");
  if (!button) return;
  const form = document.getElementById("wallet-anchor-form");
  const status = document.getElementById("wallet-status");
  const d = button.dataset;
  const chainIdHex = "0x" + Number(d.chainId).toString(16);
  const say = (message) => { status.textContent = message; };
  const errorCode = (err) => err?.code ?? err?.data?.originalError?.code;

  async function ensureNetwork() {
    try {
      await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: chainIdHex }] });
    } catch (err) {
      if (errorCode(err) !== 4902) throw err;
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: chainIdHex,
          chainName: d.chainName,
          rpcUrls: [d.rpcUrl],
          blockExplorerUrls: [d.explorerUrl],
          nativeCurrency: { name: d.currency, symbol: d.currency, decimals: 18 },
        }],
      });
    }
  }

  button.addEventListener("click", async () => {
    if (!window.ethereum) {
      say("MetaMask was not detected. Install the MetaMask extension or use the server wallet.");
      return;
    }
    button.disabled = true;
    try {
      say("Connecting to MetaMask…");
      const [from] = await window.ethereum.request({ method: "eth_requestAccounts" });
      say("Switching to " + d.chainName + "…");
      await ensureNetwork();
      say("Confirm the transaction in MetaMask…");
      const txHash = await window.ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from, to: d.contract, data: d.calldata }],
      });
      say("Transaction sent. Verifying on " + d.chainName + "…");
      form.elements.tx_hash.value = txHash;
      form.submit();
    } catch (err) {
      say(errorCode(err) === 4001 ? "Request rejected in MetaMask." : "MetaMask error: " + (err?.message || err));
      button.disabled = false;
    }
  });
})();
