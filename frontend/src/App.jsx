// App.jsx
import React, { useState, useEffect, createContext, useContext, useRef } from 'react';
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, onAuthStateChanged } from 'firebase/auth';
import { getFirestore, doc, setDoc, onSnapshot, collection, query, orderBy, getDoc } from 'firebase/firestore'; 

import * as pdfjsLib from 'pdfjs-dist/build/pdf.min.mjs';
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';


// --- Firebase Configuration (Provided by user - directly embedded) ---
const appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';

const firebaseConfig = {
    apiKey: "AIzaSyCGNrraTI7phIwC-z4kFEtcOTjtEHlka4U",
    authDomain: "gpdf-c00c6.firebaseapp.com",
    projectId: "gpdf-c00c6",
    storageBucket: "gpdf-c00c6.firebasestorage.app", // Still needed for Firebase init, but not for direct file uploads
    messagingSenderId: "1078821179939",
    appId: "1:1078821179939:web:1e15aaddf146139c225796"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

// --- Contexts for Global State Management ---

// Auth Context
const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
    const [currentUser, setCurrentUser] = useState(null);
    const [loadingAuth, setLoadingAuth] = useState(true);
    // db ve auth instance'larını AuthContext'e ekle
    const [firestoreDb, setFirestoreDb] = useState(null); 
    const [firebaseAuth, setFirebaseAuth] = useState(null);

    useEffect(() => {
        // Firebase init'i burada yap, böylece db ve auth instance'ları context'e sağlanabilir
        setFirestoreDb(getFirestore(app));
        setFirebaseAuth(getAuth(app));

        const unsubscribe = onAuthStateChanged(auth, async (user) => {
            if (user) {
                setCurrentUser(user);
            } else {
                try {
                    await signInAnonymously(auth);
                } catch (error) {
                    console.error("Error signing in anonymously:", error);
                }
            }
            setLoadingAuth(false);
        });
        return () => unsubscribe();
    }, []);

    return (
        <AuthContext.Provider value={{ currentUser, loadingAuth, db: firestoreDb, auth: firebaseAuth }}> {/* db ve auth eklendi */}
            {children}
        </AuthContext.Provider>
    );
};

// Theme Context
const ThemeContext = createContext(null);

export const ThemeProvider = ({ children }) => {
    const [theme, setTheme] = useState(() => {
        if (typeof window !== 'undefined') {
            return localStorage.getItem('theme') || 'light';
        }
        return 'light';
    });

    useEffect(() => {
        if (typeof window !== 'undefined') {
            document.documentElement.classList.remove('light', 'dark');
            document.documentElement.classList.add(theme);
            localStorage.setItem('theme', theme);
        }
    }, [theme]);

    const toggleTheme = () => {
        setTheme((prevTheme) => (prevTheme === 'light' ? 'dark' : 'light'));
    };

    return (
        <ThemeContext.Provider value={{ theme, toggleTheme }}>
            {children}
        </ThemeContext.Provider>
    );
};

// --- Custom Modal for Messages (instead of alert/confirm) ---
const MessageModal = ({ message, onClose, type = 'info' }) => {
    if (!message) return null;

    const bgColor = type === 'error' ? 'bg-red-500' : 'bg-blue-500';
    const textColor = 'text-white';

    return (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center z-50">
            <div className={`p-6 rounded-lg shadow-xl ${bgColor} ${textColor} max-w-sm w-full mx-4`}>
                <p className="text-lg font-semibold mb-4">{message}</p>
                <button
                    onClick={onClose}
                    className="w-full py-2 px-4 rounded-md bg-white text-gray-800 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-300 transition-colors"
                >
                    OK
                </button>
            </div>
        </div>
    );
};

// --- Navigation Bar Component ---
const Navbar = () => {
    const { theme, toggleTheme } = useContext(ThemeContext);
    const { currentUser } = useContext(AuthContext); // currentUser'ı buradan al

    return (
        <nav className="bg-white dark:bg-gray-800 shadow-md p-4 w-full">
            <div className="max-w-screen-xl mx-auto flex justify-between items-center">
                <div className="text-2xl font-bold text-gray-800 dark:text-gray-100">
                    G-PDF Translator
                </div>
                <div className="flex items-center space-x-4">
                    {currentUser && (
                        <span className="text-gray-600 dark:text-gray-300 text-sm">User ID: {currentUser.uid}</span>
                    )}
                    <button
                        onClick={toggleTheme}
                        className="px-4 py-2 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 shadow-sm hover:shadow-md transition-all duration-300 text-sm font-medium"
                    >
                        {theme === 'light' ? 'Dark Mode' : 'Light Mode'}
                    </button>
                </div>
            </div>
        </nav>
    );
};

// --- Footer Component ---
const Footer = () => {
    return (
        <footer className="bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 p-6 text-center text-sm mt-auto">
            <div className="max-w-screen-xl mx-auto flex flex-col md:flex-row justify-between items-center space-y-4 md:space-y-0">
                <p>&copy; {new Date().getFullYear()} G-PDF Translator. All rights reserved.</p>
                <div className="flex space-x-6">
                    <a href="#" className="hover:text-blue-600 dark:hover:text-blue-400 transition-colors duration-200">Contact Me</a>
                    <a href="mailto:aligoek@outlook.com" className="hover:text-blue-600 dark:hover:text-blue-400 transition-colors duration-200">aligoek@outlook.com</a>
                    
                </div>
            </div>
        </footer>
    );
};


// --- Main PDF Translator Component ---
const PDFTranslator = () => {
    const { currentUser, loadingAuth, db } = useContext(AuthContext); // db'yi buradan al
    const { theme } = useContext(ThemeContext);

    const [selectedFile, setSelectedFile] = useState(null);
    const [targetLanguage, setTargetLanguage] = useState('tr');
    const [uploadProgress, setUploadProgress] = useState(0);
    const [translationStatus, setTranslationStatus] = useState('idle');
    const [translatedContent, setTranslatedContent] = useState('');
    const [currentTaskId, setCurrentTaskId] = useState(null);
    const [modalMessage, setModalMessage] = useState('');
    const [modalType, setModalType] = useState('info');

    // --- UPDATED: Using the correct Cloud Function URL ---
    const RENDER_BACKEND_URL = 'https://us-central1-gpdf-c00c6.cloudfunctions.net/pdf_translator_app'; 

    const languages = [
        { code: 'en', name: 'English' },
        { code: 'tr', name: 'Turkish' },
        { code: 'es', name: 'Spanish' },
        { code: 'fr', name: 'French' },
        { code: 'de', name: 'German' },
        { code: 'it', name: 'Italian' },
        { code: 'pt', name: 'Portuguese' },
        { code: 'ru', name: 'Russian' },
        { code: 'zh-cn', name: 'Chinese (Simplified)' },
        { code: 'ja', name: 'Japanese' },
    ];

    // Listen for translation task updates from Firestore
    useEffect(() => {
        let unsubscribe;
        // userId'yi currentUser'dan al, db'yi useContext'ten al
        const userId = currentUser?.uid; 

        if (currentUser && currentTaskId && db && userId) { 
            console.log("useEffect: Setting up Firestore listener for task:", currentTaskId);
            const taskDocRef = doc(db, `artifacts/${appId}/users/${userId}/translations`, currentTaskId);
            
            unsubscribe = onSnapshot(taskDocRef, (docSnap) => {
                console.log(`[onSnapshot] Listener triggered for task: ${currentTaskId}. Document exists: ${docSnap.exists()}`);
                if (docSnap.exists()) {
                    const data = docSnap.data();
                    console.log(`[onSnapshot] Data received for task ${currentTaskId}:`, data);
                    console.log(`[onSnapshot] UI state updated: status=${data.status}, progress=${data.progress}`);
                    setTranslationStatus(data.status || 'processing');
                    setUploadProgress(data.progress || 0);
                    if (data.translatedContent) {
                        setTranslatedContent(data.translatedContent.join('\n\n'));
                    }
                    if (data.status === 'completed' || data.status === 'failed') {
                        console.log(`[onSnapshot] Task ${currentTaskId} completed/failed. Status: ${data.status}. Clearing currentTaskId.`);
                        setCurrentTaskId(null); 
                    }
                } else {
                    console.log(`[onSnapshot] Firestore document for task ${currentTaskId} no longer exists.`);
                    setTranslationStatus('failed');
                    setModalMessage("Translation task document disappeared from Firestore.");
                    setModalType('error');
                    setCurrentTaskId(null);
                }
            }, (error) => {
                console.error(`[onSnapshot] Error listening to task ${currentTaskId}:`, error);
                setModalMessage("Error fetching translation status. Please try again.");
                setModalType('error');
                setTranslationStatus('failed');
            });
        } else {
            console.log("useEffect: Not setting up listener. currentUser:", !!currentUser, "currentTaskId:", currentTaskId, "db:", !!db);
        }
        return () => {
            if (unsubscribe) {
                console.log(`useEffect: Cleaning up Firestore listener for task: ${currentTaskId} (if active).`);
                unsubscribe();
            }
        };
    }, [currentUser, currentTaskId, db, appId]); // db bağımlılıklara eklendi

    const handleFileChange = (event) => {
        const file = event.target.files[0];
        if (file && file.type === 'application/pdf') {
            setSelectedFile(file);
            setTranslatedContent('');
            setTranslationStatus('idle');
            setUploadProgress(0);
            setModalMessage('');
        } else {
            setSelectedFile(null);
            setModalMessage("Please select a valid PDF file.");
            setModalType('error');
        }
    };

    const handleDragOver = (event) => {
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = 'copy';
    };

    const handleDrop = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const file = event.dataTransfer.files[0];
        if (file && file.type === 'application/pdf') {
            setSelectedFile(file);
            setTranslatedContent('');
            setTranslationStatus('idle');
            setUploadProgress(0);
            setModalMessage('');
        } else {
            setModalMessage("Please drop a valid PDF file.");
            setModalType('error');
        }
    };

    const handleTranslate = async () => {
        if (!selectedFile) {
            setModalMessage("Please select a PDF file first.");
            setModalType('error');
            return;
        }
        if (!currentUser) {
            setModalMessage("Authentication not ready. Please wait a moment.");
            setModalType('error');
            return;
        }
        if (!db) { // db'nin hazır olduğundan emin ol
            setModalMessage("Firestore database not initialized. Please wait.");
            setModalType('error');
            return;
        }

        setTranslationStatus('uploading'); // This will now represent "reading file"
        setUploadProgress(0);
        setTranslatedContent('');
        setModalMessage('');

        try {
            const reader = new FileReader();
            reader.readAsArrayBuffer(selectedFile);

            reader.onloadstart = () => {
                setUploadProgress(0);
                console.log("[handleTranslate] File reading started. Progress: 0%");
            };
            reader.onprogress = (event) => {
                if (event.lengthComputable) {
                    const progress = (event.loaded / event.total) * 100;
                    setUploadProgress(progress);
                    console.log(`[handleTranslate] File reading progress: ${progress.toFixed(1)}%`);
                }
            };

            reader.onload = async (e) => {
                const arrayBuffer = e.target.result;
                const base64Pdf = btoa(
                    new Uint8Array(arrayBuffer)
                        .reduce((data, byte) => data + String.fromCharCode(byte), '')
                );
                console.log("[handleTranslate] File reading completed. Converting to Base64.");

                const userId = currentUser.uid;
                const fileName = selectedFile.name;

                const taskRef = doc(collection(db, `artifacts/${appId}/users/${userId}/translations`));
                const taskId = taskRef.id;
                setCurrentTaskId(taskId); // currentTaskId'yi hemen ayarla ki listener kurulabilsin
                console.log("[handleTranslate] New task ID generated and set:", taskId);

                console.log("[handleTranslate] Attempting to set Firestore document for task:", taskId);
                await setDoc(taskRef, {
                    fileName: fileName,
                    targetLanguage: targetLanguage,
                    status: 'processing',
                    progress: 0,
                    timestamp: new Date(),
                });
                console.log("[handleTranslate] Firestore document for new task initiated successfully.");

                setTranslationStatus('processing'); // UI'ı hemen güncelle
                console.log("[handleTranslate] UI status set to 'processing'.");

                try {
                    // The backend URL is now correct. The path should be /translate
                    const backendUrl = `${RENDER_BACKEND_URL}/translate`; 
                    console.log("[handleTranslate] Sending request to backend for translation to:", backendUrl);
                    const response = await fetch(backendUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            taskId: taskId,
                            userId: userId,
                            fileName: fileName,
                            pdfContent: base64Pdf,
                            targetLanguage: targetLanguage,
                        }),
                    });
                    console.log("[handleTranslate] Backend fetch response received.");

                    // --- Improved Error Handling ---
                    if (!response.ok) {
                        let errorDetails = `Status: ${response.status} ${response.statusText || ''}`;
                        try {
                            const errorData = await response.json();
                            errorDetails = `Backend error: ${errorData.error || JSON.stringify(errorData)}`;
                        } catch (jsonParseError) {
                            const rawText = await response.text();
                            errorDetails = `Backend error (not JSON): ${rawText.substring(0, 200)}...`;
                        }
                        throw new Error(errorDetails);
                    }

                    const result = await response.json();
                    console.log("[handleTranslate] Backend response (successful):", result);
                    
                    // The onSnapshot listener will handle the UI updates from here.
                    // No need for manual refetching.

                } catch (backendError) {
                    console.error("[handleTranslate] Error triggering backend translation:", backendError);
                    setModalMessage(`Failed to trigger backend translation: ${backendError.message}`);
                    setModalType('error');
                    setTranslationStatus('failed');
                    if (taskRef) {
                        await setDoc(taskRef, { status: 'failed', errorMessage: backendError.message }, { merge: true });
                    }
                }
            };

            reader.onerror = (error) => {
                console.error("[handleTranslate] Error reading file:", error);
                setModalMessage("Error reading PDF file. Please try again.");
                setModalType('error');
                setTranslationStatus('failed');
            };

        } catch (error) {
            console.error("[handleTranslate] Error initiating translation (overall):", error);
            setModalMessage(`Error initiating translation: ${error.message}`);
            setModalType('error');
            setTranslationStatus('failed');
        }
    };

    const handleDownloadPdf = async () => {
        if (!translatedContent) {
            setModalMessage("No translated content to generate PDF.");
            setModalType('info');
            return;
        }

        try {
            setModalMessage("Generating PDF, please wait...");
            setModalType('info');

            // The backend URL is now correct. The path should be /generate-pdf
            const backendUrl = `${RENDER_BACKEND_URL}/generate-pdf`;
            const response = await fetch(backendUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    translatedContent: translatedContent,
                    originalFileName: selectedFile ? selectedFile.name : 'document',
                    targetLanguage: targetLanguage,
                }),
            });

            if (!response.ok) {
                let errorDetails = `Status: ${response.status} ${response.statusText || ''}`;
                try {
                    const errorData = await response.json();
                    errorDetails = `PDF generation failed: ${errorData.error || JSON.stringify(errorData)}`;
                } catch (e) {
                     const rawText = await response.text();
                    errorDetails = `PDF generation failed (Non-JSON response): ${rawText.substring(0, 200)}...`;
                }
                throw new Error(errorDetails);
            }

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${selectedFile.name.split('.')[0]}_translated_${targetLanguage}.pdf`;
            document.body.appendChild(a);
a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            setModalMessage("PDF generated and downloaded successfully!");
            setModalType('info');

        } catch (error) {
            console.error("Error generating PDF:", error);
            setModalMessage(`Failed to generate PDF: ${error.message}`);
            setModalType('error');
        }
    };

    const isProcessing = translationStatus !== 'idle' && translationStatus !== 'completed' && translationStatus !== 'failed';

    if (loadingAuth) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
                <div className="text-xl font-semibold">Loading authentication...</div>
            </div>
        );
    }

    return (
        <div className={`min-h-screen flex flex-col ${theme === 'dark' ? 'bg-gray-900 text-gray-100' : 'bg-gray-50 text-gray-900'} font-inter transition-colors duration-300`}>
            <Navbar />
            <MessageModal message={modalMessage} onClose={() => setModalMessage('')} type={modalType} />

            <main className="flex-grow flex flex-col items-center justify-center p-4 md:p-8">
                <div className="w-full max-w-sm md:max-w-2xl lg:max_w-4xl bg-white dark:bg-gray-800 shadow-xl rounded-xl p-6 md:p-8 space-y-6 md:space-y-8 border border-gray-200 dark:border-gray-700">
                    {/* User ID Display */}
                    {currentUser && (
                        <div className="text-sm text-gray-600 dark:text-gray-400 mb-4 text-center">
                            User ID: <span className="font-mono bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded-md">{currentUser.uid}</span>
                        </div>
                    )}

                    {/* File Upload Section */}
                    <div
                        className="border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg p-6 md:p-8 text-center cursor-pointer hover:border-blue-500 hover:bg-gray-50 dark:hover:bg-gray-700 transition-all duration-300"
                        onDragOver={handleDragOver}
                        onDrop={handleDrop}
                        onClick={() => document.getElementById('fileInput').click()}
                    >
                        <input
                            type="file"
                            id="fileInput"
                            accept=".pdf"
                            onChange={handleFileChange}
                            className="hidden"
                        />
                        <svg xmlns="http://www.w3.org/2000/svg" className="mx-auto h-12 w-12 text-gray-400 dark:text-gray-500 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 0115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v8" />
                        </svg>
                        <p className="text-lg font-medium text-gray-700 dark:text-gray-300">
                            {selectedFile ? selectedFile.name : "Drag & Drop your PDF here, or Click to Select"}
                        </p>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                            Only PDF files are supported.
                        </p>
                    </div>

                    {/* Language Selection and Translate Button */}
                    <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                        <div className="w-full sm:w-auto">
                            <label htmlFor="languageSelect" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Translate to:
                            </label>
                            <div className="relative">
                                <select
                                    id="languageSelect"
                                    value={targetLanguage}
                                    onChange={(e) => setTargetLanguage(e.target.value)}
                                    className="block w-full py-3 px-4 pr-10 border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 appearance-none transition-colors duration-300 text-gray-900 dark:text-gray-100"
                                    disabled={isProcessing}
                                >
                                    {languages.map((lang) => (
                                        <option key={lang.code} value={lang.code}>
                                            {lang.name}
                                        </option>
                                    ))}
                                </select>
                                <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-gray-700 dark:text-gray-300">
                                    <svg className="fill-current h-4 w-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M9.293 12.95l.707.707L15.657 8l-1.414-1.414L10 10.828 5.757 6.586 4.343 8z"/></svg>
                                </div>
                            </div>
                        </div>
                        <button
                            onClick={handleTranslate}
                            disabled={!selectedFile || isProcessing || !currentUser}
                            className={`w-full sm:w-auto py-3 px-8 mt-7 rounded-full font-semibold text-lg shadow-md transition-all duration-300
                                ${!selectedFile || isProcessing || !currentUser
                                    ? 'bg-gray-300 dark:bg-gray-600 text-gray-500 cursor-not-allowed'
                                    : 'bg-gradient-to-r from-blue-500 to-purple-600 text-white hover:from-blue-600 hover:to-purple-700 hover:shadow-lg transform hover:-translate-y-0.5'
                                }`}
                        >
                            {isProcessing ? (
                                <span className="flex items-center justify-center">
                                    <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                    </svg>
                                    {translationStatus === 'uploading' ? 'Reading File...' : 
                                     translationStatus === 'processing' ? 'Processing...' :
                                     'Translating...'}
                                </span>
                            ) : 'Translate PDF'}
                        </button>
                    </div>

                    {/* Progress Indicator */}
                    {isProcessing && (
                        <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3 mt-6">
                            <div
                                className="bg-blue-500 h-3 rounded-full transition-all duration-500 ease-out"
                                style={{ width: `${uploadProgress}%` }}
                            ></div>
                            <p className="text-sm text-gray-600 dark:text-gray-400 text-center mt-2">
                                {translationStatus === 'uploading' && `Reading File: ${uploadProgress.toFixed(1)}%`} 
                                {translationStatus === 'processing' && `Processing: ${uploadProgress.toFixed(1)}%`}
                                {translationStatus === 'translating' && `Translating: ${uploadProgress.toFixed(1)}%`}
                            </p>
                        </div>
                    )}

                    {/* Translated Content Download Button (PDF only) */}
                    {translationStatus === 'completed' && translatedContent && (
                        <div className="mt-8 p-4 bg-gray-100 dark:bg-gray-700 rounded-lg shadow-inner border border-gray-200 dark:border-gray-600 text-center"> 
                            <p className="text-lg font-medium text-gray-700 dark:text-gray-300 mb-4">Translation completed successfully!</p> 
                            <button
                                onClick={handleDownloadPdf}
                                className="w-full py-3 px-8 rounded-full font-semibold text-lg shadow-md transition-all duration-300 bg-gray-600 text-white hover:bg-gray-700 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600 hover:shadow-lg transform hover:-translate-y-0.5"
                            >
                                Download Translated PDF
                            </button>
                        </div>
                    )}

                    {/* Error Messages */}
                    {translationStatus === 'failed' && (
                        <div className="mt-6 p-4 rounded-lg bg-red-100 dark:bg-red-800 text-red-800 dark:text-red-200 font-medium text-center">
                            Translation failed. Please try again.
                        </div>
                    )}
                </div>
            </main>
            <Footer />
        </div>
    );
};

// --- App Root Component ---
const App = () => {
    return (
        <ThemeProvider>
            <AuthProvider>
                <PDFTranslator />
            </AuthProvider>
        </ThemeProvider>
    );
};

export default App;
