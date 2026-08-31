
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js");
 
// TODO: paste the same config you used in firebase-init.js
const firebaseConfig = {
  apiKey: "AIzaSyBcRZtbR9Ik_13FJvpkzjpH1zbcE8VLgT0",
  authDomain: "futsal-scheduler-6af0b.firebaseapp.com",
  projectId: "futsal-scheduler-6af0b",
  storageBucket: "futsal-scheduler-6af0b.firebasestorage.app",
  messagingSenderId: "397892353797",
  appId: "1:397892353797:web:1b87ee43a2c6d3cd92016c",
  measurementId: "G-CPT5FF980G"
};
 
const messaging = firebase.messaging();
 
// Fires when a push arrives while the tab is closed/backgrounded
messaging.onBackgroundMessage((payload) => {
  const title = payload.notification?.title || "Reminder";
  const body = payload.notification?.body || "";
  self.registration.showNotification(title, {
    body,
    icon: "/static/icon.png", // optional: add a small icon at this path if you have one
  });
});
 