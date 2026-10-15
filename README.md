# Thistle & Thread: handcrafted gift shop

## Run in VS Code
1. Open this folder in VS Code (File > Open Folder).
2. Open the terminal (Ctrl+`) and run:

       python -m venv venv
       venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
       pip install -r requirements.txt
       python app.py

3. Visit http://127.0.0.1:5000 for the shop and http://127.0.0.1:5000/admin for orders
   (any username, password `changeme`, or set the ADMIN_PASSWORD environment variable).

Or press F5 to run it with the included launch config.

## Edit products and photos
- Products, full descriptions and details live in `products_data.py`.
- Product images are in `static/images/`. To use real photos, copy your own jpg/png/webp
  files there and change the image filename in `products_data.py`.
- Delete `shop.db` after editing products so the database reseeds.

## Online payments (Razorpay)
Checkout offers "Pay online" (UPI, cards, netbanking, wallets) and "Cash on delivery".
Online payment turns on automatically when the keys below are set; without them the shop works as before.

1. Create a Razorpay account and copy the **Test Mode** keys from Dashboard > Account & Settings > API Keys.
2. Set environment variables before running (or add them to `.vscode/launch.json` under `env`):

       RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
       RAZORPAY_KEY_SECRET=xxxxxxxx
       RAZORPAY_WEBHOOK_SECRET=any-long-random-string

   Windows (PowerShell): `$env:RAZORPAY_KEY_ID="rzp_test_xxx"`; Mac/Linux: `export RAZORPAY_KEY_ID=rzp_test_xxx`
3. `pip install -r requirements.txt` again (adds `requests`), then run the app.
4. Test with Razorpay's test UPI id `success@razorpay` or test card 4111 1111 1111 1111 (any future expiry, any CVV).
5. Webhook (needed once the site is public, recommended): Dashboard > Settings > Webhooks > add
   `https://YOUR-DOMAIN/api/payment/webhook`, secret = `RAZORPAY_WEBHOOK_SECRET`, events `payment.captured` and `order.paid`.
   It confirms payments even if the customer closes the tab right after paying.
   For local testing use a tunnel such as `ngrok http 5000`.
6. To go live, complete Razorpay KYC and swap in the **Live** keys.

How it works: the server calculates the total from the database and creates the Razorpay order; the browser opens
Razorpay Checkout; the payment signature is verified on the server before the order moves from "Pending payment"
to "New". Orders are never packed until paid. If a paid order is cancelled it is marked "Refund due" in `/admin`;
issue the refund from the Razorpay dashboard and set the payment to "Refunded".
