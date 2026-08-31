// TODO: paste your Firebase web app config here (same values as firebase-messaging-sw.js)
const firebaseConfig = {
  apiKey: "AIzaSyBcRZtbR9Ik_13FJvpkzjpH1zbcE8VLgT0",
  authDomain: "futsal-scheduler-6af0b.firebaseapp.com",
  projectId: "futsal-scheduler-6af0b",
  storageBucket: "futsal-scheduler-6af0b.firebasestorage.app",
  messagingSenderId: "397892353797",
  appId: "1:397892353797:web:1b87ee43a2c6d3cd92016c",
  measurementId: "G-CPT5FF980G"
};
 
// TODO: paste your Web Push certificate (VAPID key) from
// Firebase Console > Project settings > Cloud Messaging > Web configuration
const VAPID_KEY = "BH7e2zft_Z7vCoH1ihDpEVJvRkm_XZ2lll185SG0sNiUihHnKBQTXGYT9rPvdbs3Y6I_TfZAgZ6gi5Wm8QERx1M";
 
firebase.initializeApp(firebaseConfig);
const messaging = firebase.messaging();
 
async function setupNotifications() {
  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    console.warn("Push notifications aren't supported in this browser.");
    return;
  }
 
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      console.info("Notification permission not granted.");
      return;
    }
 
    const registration = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
 
    const token = await messaging.getToken({
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: registration,
    });
 
    if (!token) {
      console.warn("No registration token available.");
      return;
    }
 
    await fetch("/register-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch (err) {
    console.error("Notification setup failed:", err);
  }
}
 
// Shows a fading in-page toast when a push arrives while the tab is open and focused
messaging.onMessage((payload) => {
  const title = payload.notification?.title || "Reminder";
  const body  = payload.notification?.body  || "";

  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = `<strong>${title}</strong>${body}`;
  container.appendChild(toast);

  // Remove from DOM after the CSS animation completes (5 s)
  toast.addEventListener("animationend", () => toast.remove());
});
 
setupNotifications();