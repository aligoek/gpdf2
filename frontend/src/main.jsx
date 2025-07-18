import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './index.css'; // Your Tailwind CSS import

// Import Firebase functions for initialization
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';
import { getStorage } from 'firebase/storage';

// PDF.js worker setup
import * as pdfjsLib from 'pdfjs-dist';
// IMPORTANT: This path assumes you have copied 'pdf.worker.js'
// from 'node_modules/pdfjs-dist/build/' directly into your 'public/' folder.
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.js'; // Direct path to the worker in public folder

// Your Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyCGNrraTI7phIwC-z4kFEtcOTjtEHlka4U",
  authDomain: "gpdf-c00c6.firebaseapp.com",
  projectId: "gpdf-c00c6",
  storageBucket: "gpdf-c00c6.firebasestorage.app",
  messagingSenderId: "1078821179939",
  appId: "1:1078821179939:web:1e15aaddf146139c225796"
};

// Initialize Firebase App
const app = initializeApp(firebaseConfig);

// Get Firebase service instances
const auth = getAuth(app);
const db = getFirestore(app);
const storage = getStorage(app);

// Define global-like variables for Canvas environment compatibility
window.__app_id = firebaseConfig.projectId || 'default-app-id'; // Use Firebase projectId as a unique app ID
window.__firebase_config = JSON.stringify(firebaseConfig);
window.__initial_auth_token = ''; // Leave empty for anonymous sign-in in local development

// Sign in anonymously immediately to get a userId for Firestore/Storage operations
// This ensures that Firebase services are ready when the App component mounts.
signInAnonymously(auth).catch(e => console.error("Anonymous sign-in failed:", e));


ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Pass Firebase instances as props to the App component */}
    <App auth={auth} db={db} storage={storage} />
  </React.StrictMode>,
);
