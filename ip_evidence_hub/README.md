# IP Evidence Hub

A Flask web application for intellectual-property evidence management: fingerprint files with SHA-256, keep them off-chain, and anchor their hashes on the MST blockchain (MST Testnet, Coral Testnet or Mainnet).

## Features

**Accounts and security**
- Registration, login, logout (POST with CSRF token) and change password
- Password hashing, CSRF protection on every form, HttpOnly/SameSite session cookies with an 8-hour lifetime
- Login lockout after 5 failed attempts per IP + account for 15 minutes
- Security headers (CSP, X-Frame-Options, nosniff, Referrer-Policy)
- User/admin roles

**Evidence**
- Uploads with extension and size checks, SHA-256 fingerprints and integrity status
- Off-chain local file storage with SQLite metadata
- Duplicate detection: warns when an identical file was already recorded
- Version history: register new versions of a work linked to the original
- Image watermarking with Pillow (original is preserved)
- PDF evidence certificate with QR code, including blockchain transaction details
- Dashboard search (title, description, filename, hash prefix) and blockchain-status filter

**Blockchain (MST)**
- Anchor a hash with **MetaMask** (user's wallet signs in the browser) or with a **server wallet** (key in `.env`)
- The server verifies every transaction on-chain: successful receipt, sent to the configured registry contract, and an `EvidenceRegistered` event for this exact hash
- Pending transactions can be refreshed; explorer links on the detail page, public page and certificate

**Public tools**
- Verify a file against a known hash
- Hash lookup: by hash or file, shows the earliest record in the hub and the on-chain registration
- Public verification page per record (linked from the certificate QR code)

**Billing (Razorpay test mode or simulated gateway)**
- First 2 evidence records per user are free (`FREE_EVIDENCE_LIMIT`)
- After that: buy single evidence credits or a 30-day subscription with unlimited uploads
- With Razorpay test keys: the real Razorpay popup (UPI, cards, netbanking) in test mode — no real money moves. The server verifies Razorpay's payment signature before granting anything, and refuses live keys
- Without keys: a simulated checkout with "Pay" and "Simulate failed payment"
- Each payment can be completed only once; subscriptions bought while active extend the end date
- Admin accounts are never charged

**Administration**
- Review queue: set evidence status (Pending review, Under review, Approved, Rejected, Needs more info) with a note to the owner
- Payments page with total revenue, active subscribers and payment history
- Grant free evidence credits to a user from the Users page
- Promote or demote users (admins cannot change their own role)
- System-wide audit log with action filter

## Quick start

Python 3.10+ recommended.

### 1. Create environment

Windows PowerShell:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Linux/macOS:
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```
For running tests: `pip install -r requirements-dev.txt`

### 3. Configure environment
Copy `.env.example` to `.env` and set at least `FLASK_SECRET_KEY` and `ADMIN_PASSWORD`. If `FLASK_SECRET_KEY` is not set, a random key is generated at startup and all sessions end when the app restarts.

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_SECRET_KEY` | random per start | Session signing key |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin` / `admin@example.com` / `ChangeMe123!` | Seeded once, when no admin exists |
| `DATABASE_PATH` | `ip_evidence.db` | SQLite file (relative to the app folder or absolute) |
| `STORAGE_PATH` | `storage` | Off-chain file storage folder |
| `MAX_UPLOAD_MB` | `25` | Upload size limit |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `FREE_EVIDENCE_LIMIT` | `2` | Free evidence records per user |
| `PRICE_PER_EVIDENCE` | `49` | Price of one evidence credit |
| `SUBSCRIPTION_PRICE` / `SUBSCRIPTION_DAYS` | `299` / `30` | Subscription price and length |
| `CURRENCY_SYMBOL` | `₹` | Shown on prices |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | empty | Razorpay keys; empty = simulated checkout |
| `RAZORPAY_MODE` | `test` | `test` (requires `rzp_test_` keys) or `live` (requires `rzp_live_` keys); a mismatch stops the app from starting |
| `RAZORPAY_WEBHOOK_SECRET` | empty | Secret set on the Razorpay webhook; enables `/webhooks/razorpay` |
| `BUSINESS_NAME`, `SUPPORT_EMAIL`, `SUPPORT_PHONE`, `BUSINESS_ADDRESS` | empty | Shown on the Terms, Privacy, Refunds and Contact pages |
| `RAZORPAY_CURRENCY` | `INR` | Currency sent to Razorpay |
| `MST_PAYMENT_ADDRESS` | empty | Public address of the project wallet that receives MetaMask payments; empty = MetaMask payments off |
| `PRICE_PER_EVIDENCE_MST` / `SUBSCRIPTION_PRICE_MST` | `1` / `5` | MetaMask prices in tMSTC |
| `MST_NETWORK` | `testnet` | `testnet`, `coral-testnet` or `mainnet` |
| `CONTRACT_ADDRESS` | empty | Deployed `IPEvidenceRegistry` address; anchoring is disabled until set |
| `MST_SIGNER_PRIVATE_KEY` | empty | Optional server wallet key; enables "Anchor with server wallet" |
| `MST_RPC_URL` / `MST_EXPLORER_URL` / `MST_CURRENCY_SYMBOL` | network defaults | Optional overrides |

### 4. Run
```bash
python app.py
```
Open http://127.0.0.1:5000 and log in with the admin account. `/health` reports the selected network and whether a contract is configured.

### 5. Test
```bash
python -m pytest
```
Tests use an in-memory stand-in for the MST chain; they do not contact any network.

## MST network settings

| | MST Testnet (default) | Coral Testnet | Mainnet |
|---|---|---|---|
| `MST_NETWORK` | `testnet` | `coral-testnet` | `mainnet` |
| Chain ID | `91562037` | `54635` | `4646` |
| RPC | `https://testnetrpc.mstblockchain.com` | `https://coralrpc.mstblockchain.com` | `https://4646.rpc.thirdweb.com` |
| Explorer | `https://testnet.mstscan.com` | `https://coral.mstscan.com` | `https://mstscan.com` |
| Currency | `tMSTC` | `MST` (unverified) | `MST` |

MST Testnet is the default because MST's developer documentation lists the official faucet, https://faucet.mstblockchain.com, for it: paste your wallet address there to receive free test tMSTC. No Coral Testnet faucet was found in MST's documentation.

On the first chain call the app checks that the RPC's chain ID matches the selected network and refuses to continue if it does not. Verify endpoints against MST's developer documentation (https://docs.mstblockchain.com) before deploying. The original starter README mentioned a deprecation notice for MST Testnet after 2027-01-31; this was not confirmed in MST's current docs, so check before relying on it long term.

## Razorpay test mode

1. Sign up at https://dashboard.razorpay.com/signup (test mode needs no KYC).
2. Make sure the dashboard is in **Test Mode** (toggle at the top).
3. Go to **Account & Settings → API Keys → Generate Test Key**, and copy the Key ID and Key Secret.
4. Add them to `.env` (never commit them):
   ```
   RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
   RAZORPAY_KEY_SECRET=xxxxxxxx
   ```
5. Restart the app. Billing now opens the Razorpay popup. Pay with Razorpay's test details, for example UPI ID `success@razorpay` (or `failure@razorpay` to test a failure), or a test card from Razorpay's documentation.

Test payments appear in the Razorpay dashboard under **Transactions → Payments** (test mode) and on the app's admin **Payments** page with the Razorpay payment ID.

### Webhook (recommended in test, required for live)
The webhook credits a payment even if the user closes the browser before returning to the app. It needs a public HTTPS URL (e.g. your Render URL).
1. Razorpay dashboard → **Account & Settings → Webhooks → Add New Webhook**.
2. URL: `https://<your-app>/webhooks/razorpay`; choose a secret; events: **payment.captured** and **order.paid**.
3. Set the same secret as `RAZORPAY_WEBHOOK_SECRET`.

The app verifies the `X-Razorpay-Signature` header, checks the amount matches the order, and credits each payment only once (browser confirmation and webhook cannot double-credit).

## MetaMask payments (MST Testnet only)

Users can pay for evidence credits or subscriptions with free **tMSTC** test tokens. This only works when `MST_NETWORK=testnet`; on any other network the option is hidden and the server refuses MetaMask payments, so no real crypto can be taken.

1. In MetaMask create a separate account to act as the **project wallet** and copy its public address.
2. Set `MST_NETWORK=testnet` and `MST_PAYMENT_ADDRESS=0x...` (public address only — never the private key) and restart.
3. A paying user needs MetaMask with tMSTC from https://faucet.mstblockchain.com.
4. On Billing, **Pay with MetaMask** opens MetaMask, switches to MST Testnet, and sends the price to the project wallet with the payment reference in the transaction data.
5. The server checks the transaction on-chain — successful, sent to the project wallet, at least the price, carrying this payment's reference, and not already used — before granting the credit or subscription. If it is not mined yet, the payment stays pending with a **Refresh status** button on Billing.

Received tokens are visible in the project wallet and on https://testnet.mstscan.com. Refunds are sent manually from MetaMask.

## Going live with real payments

Do these in order; the app keeps taking only test payments until step 5.
1. Complete Razorpay account activation (KYC: PAN, bank account, business details). Razorpay decides approval.
2. Deploy the app on a public HTTPS URL and set `SESSION_COOKIE_SECURE=true`.
3. Set `BUSINESS_NAME`, `SUPPORT_EMAIL`, `SUPPORT_PHONE`, `BUSINESS_ADDRESS`, then review and edit the Terms, Privacy, Refunds and Contact pages (`templates/policy_*.html`). They are templates, not legal advice; have them reviewed.
4. Create a **live** webhook (same steps as above, in Live Mode) with a new secret.
5. In the hosting environment (never in Git): `RAZORPAY_MODE=live`, live `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`, live `RAZORPAY_WEBHOOK_SECRET`. Restart.
6. Make one small real payment, confirm it is credited and appears in the Razorpay live dashboard, then refund it from the dashboard.

Refunds are made from the Razorpay dashboard; the app does not issue refunds itself, so adjust the user's credits manually (Users page) when refunding.

## Smart contract deployment

1. Open `contracts/IPEvidenceRegistry.sol` in https://remix.ethereum.org and compile with Solidity 0.8.20+.
2. In MetaMask, add MST Testnet (settings above) and request free tMSTC from https://faucet.mstblockchain.com.
3. In Remix, choose **Injected Provider - MetaMask** and deploy.
4. Put the deployed address in `.env` as `CONTRACT_ADDRESS` and restart the app.
5. Optional server wallet: create a dedicated wallet, fund it with a small amount of MST, and set `MST_SIGNER_PRIVATE_KEY`. Never use a personal wallet key, never commit `.env`, and keep only small balances on the server wallet.

Deploy separately on Mainnet when you switch `MST_NETWORK=mainnet`; each network needs its own contract address.

### How anchoring works

- The contract stores `sha256 digest → (registrant, block timestamp, reference ID)`. The reference ID is `IPEH-<evidence id>`; no file content or personal data goes on-chain.
- Each hash can be registered once. If a hash is already on-chain, the app shows the existing registrant and time instead of sending a transaction.
- **MetaMask**: the evidence page builds the call data on the server, MetaMask switches to (or adds) the MST network and sends the transaction, and the server then verifies the transaction before recording it.
- **Server wallet**: the server signs and sends the transaction and waits up to 120 seconds for the receipt. If it is not mined in time, the status stays *Pending confirmation* until you click **Refresh status**.

## Limitations and production checklist

This is a runnable project, not a security-audited production system.

- A hash proves file-byte integrity. A block timestamp proves the hash existed no later than that block. Neither alone proves authorship or legal ownership.
- SQLite and local storage suit demos and single-host deployments. Production should use managed database/object storage, encrypted backups, malware scanning, email verification, password reset and MFA.
- The public verification page reveals the title, filename, hash, size and timestamps. Avoid putting personal or confidential metadata in titles or filenames.
- Image watermarking is a visible label, not an unremovable watermark.
- Run behind HTTPS with `SESSION_COOKIE_SECURE=true` and debug disabled. Login lockout is per IP + account and stored in SQLite.
- A server wallet key in `.env` is suitable for demos; production should use a secret manager or a hardware/KMS signer.

## Project layout

```text
ip_evidence_hub/
  app.py                  Flask routes, database, uploads, reports
  chain.py                MST network config and registry contract client (web3.py)
  contracts/IPEvidenceRegistry.sol
  static/style.css
  static/wallet.js        MetaMask anchoring
  templates/
  tests/                  pytest suite (FakeChain stand-in in tests/fakes.py)
  requirements.txt
  requirements-dev.txt
  storage/
```
