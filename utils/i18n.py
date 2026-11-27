"""
Internationalization (i18n) and Multilingual Localization Engine (#214).
Provides localized message catalogs and translation resolution for AI Campus Placement Agent.
Supported languages: English (en), Hindi (hi), Telugu (te), Spanish (es).
"""

from typing import Dict, Any, Optional

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ["en", "hi", "te", "es"]

LOCALE_NAMES = {
    "en": "English",
    "hi": "हिन्दी (Hindi)",
    "te": "తెలుగు (Telugu)",
    "es": "Español (Spanish)"
}

TRANSLATION_CATALOG: Dict[str, Dict[str, str]] = {
    "en": {
        "app_title": "AI Campus Placement Agent",
        "tagline": "AI-Powered Campus Placement Preparation Suite",
        "nav_home": "Home",
        "nav_login": "Login",
        "nav_register": "Register",
        "nav_logout": "Logout",
        "nav_applications": "Applications",
        "nav_history": "History",
        "nav_my_reports": "My Reports",
        "nav_compare": "Compare Versions",
        "nav_internships": "Find Internships",
        "nav_coding": "Practice DSA",
        "nav_interviews": "Interview History",
        "nav_settings": "Notification Settings",
        "nav_delete_account": "Delete Account",
        "btn_start_analysis": "Start Comprehensive Placement Analysis",
        "btn_upload_resume": "Upload PDF / DOCX",
        "btn_paste_text": "Paste Text",
        "btn_clear_form": "Clear Form",
        "btn_download_pdf": "Download Official Report",
        "label_select_company": "Target Company",
        "label_job_description": "Job Description (Optional)",
        "label_role_title": "Target Role Title",
        "status_applied": "Applied",
        "status_interview": "Interview",
        "status_selected": "Selected",
        "status_rejected": "Rejected",
        "hero_heading": "Crack Your Dream Campus Placement with Multi-Agent AI",
        "hero_subheading": "Instant ATS compatibility audits, bespoke interview simulations, and tailored skills roadmaps for leading tech recruiters.",
        "footer_copyright": "© 2026 AI Campus Placement Agent. All rights reserved.",
        "hero_title": "Get placement-ready, one focused step at a time.",
        "hero_subtitle": "Turn your resume into a clear preparation plan. Understand your role fit, close skill gaps, and practice for interviews with AI-guided feedback.",
        "hero_badge_ats": "ATS Score Analysis",
        "hero_badge_projects": "Skill Gap Projects",
        "hero_badge_interview": "Live AI Mock Interviews",
        "workspace_title": "Placement Analysis Workspace",
        "features_heading": "Everything you need to prepare with confidence",
        "feature_resume_title": "Resume analysis",
        "feature_resume_desc": "Review resume formatting, projects, experience, ATS compatibility, and targeted improvements.",
        "feature_job_title": "Job description matching",
        "feature_job_desc": "Compare skills and projects with a target role and understand where your experience aligns.",
        "feature_skill_title": "Skill-gap guidance",
        "feature_skill_desc": "Identify missing skills, understand their priority, and explore learning pathways and projects.",
        "feature_roadmap_title": "Personalized preparation roadmaps",
        "feature_roadmap_desc": "Build a six-week preparation curriculum with estimated study time, practice, and checkpoints.",
        "feature_interview_title": "Mock interview practice",
        "feature_interview_desc": "Practice tailored questions and receive feedback on your answers and communication.",
        "empty_analyses": "No placement analyses found. Upload your resume to begin preparation.",
        "empty_jobs": "No job applications tracked yet. Add your first opportunity above.",
        "lang_switch_success": "Interface language updated successfully.",
        "rate_limit_analyses_left": "analyses left this hour",
        "rate_limit_evaluations_left": "evaluations left this hour",
        "rate_limit_try_again": "You can try again in {time}",
        "rate_limit_exceeded_title": "Rate limit reached"
    },
    "hi": {
        "app_title": "एआई कैंपस प्लेसमेंट एजेंट",
        "tagline": "एआई-संचालित कैंपस प्लेसमेंट तैयारी मंच",
        "nav_home": "मुख्य पृष्ठ",
        "nav_login": "लॉग इन करें",
        "nav_register": "पंजीकरण करें",
        "nav_logout": "लॉग आउट",
        "nav_applications": "आवेदन",
        "nav_history": "इतिहास",
        "nav_my_reports": "मेरी रिपोर्ट्स",
        "nav_compare": "संस्करण तुलना",
        "nav_internships": "इंटर्नशिप खोजें",
        "nav_coding": "कोडिंग अभ्यास",
        "nav_interviews": "साक्षात्कार इतिहास",
        "nav_settings": "अधिसूचना सेटिंग्स",
        "nav_delete_account": "खाता हटाएं",
        "btn_start_analysis": "प्लेसमेंट विश्लेषण शुरू करें",
        "btn_upload_resume": "पीडीएफ / डॉक्स अपलोड करें",
        "btn_paste_text": "टेक्स्ट पेस्ट करें",
        "btn_clear_form": "फॉर्म रीसेट करें",
        "btn_download_pdf": "आधिकारिक रिपोर्ट डाउनलोड करें",
        "label_select_company": "लक्षित कंपनी",
        "label_job_description": "नौकरी विवरण (वैकल्पिक)",
        "label_role_title": "पद का शीर्षक",
        "status_applied": "आवेदन किया गया",
        "status_interview": "साक्षात्कार",
        "status_selected": "चयनित",
        "status_rejected": "अस्वीकृत",
        "hero_heading": "मल्टी-एजेंट एआई के साथ अपनी पसंदीदा नौकरी हासिल करें",
        "hero_subheading": "शीर्ष तकनीकी कंपनियों के लिए त्वरित एटीएस ऑडिट, साक्षात्कार अभ्यास और कौशल रोडमैप।",
        "footer_copyright": "© 2026 एआई कैंपस प्लेसमेंट एजेंट। सर्वाधिकार सुरक्षित।",
        "hero_title": "हर कदम पर बेहतर तैयारी करें।",
        "hero_subtitle": "अपने रिज़्यूमे से एक स्पष्ट तैयारी योजना बनाएं। भूमिका के साथ अपनी उपयुक्तता समझें, कौशल की कमियां पूरी करें और एआई-आधारित सुझावों के साथ इंटरव्यू का अभ्यास करें।",
        "hero_badge_ats": "एटीएस स्कोर विश्लेषण",
        "hero_badge_projects": "कौशल अंतर प्रोजेक्ट्स",
        "hero_badge_interview": "लाइव एआई मॉक इंटरव्यू",
        "workspace_title": "प्लेसमेंट विश्लेषण वर्कस्पेस",
        "features_heading": "आत्मविश्वास के साथ तैयारी के लिए सब कुछ",
        "feature_resume_title": "रिज़्यूमे विश्लेषण",
        "feature_resume_desc": "रिज़्यूमे का प्रारूप, प्रोजेक्ट, अनुभव, एटीएस अनुकूलता और सुधार के सुझाव देखें।",
        "feature_job_title": "नौकरी विवरण से मिलान",
        "feature_job_desc": "लक्षित भूमिका के साथ अपने कौशल और प्रोजेक्ट की तुलना करके उपयुक्तता समझें।",
        "feature_skill_title": "कौशल-अंतर मार्गदर्शन",
        "feature_skill_desc": "कमी वाले कौशल पहचानें, उनकी प्राथमिकता समझें और सीखने के संसाधन खोजें।",
        "feature_roadmap_title": "व्यक्तिगत तैयारी रोडमैप",
        "feature_roadmap_desc": "अनुमानित अध्ययन समय, अभ्यास और साप्ताहिक पड़ावों के साथ छह सप्ताह की योजना बनाएं।",
        "feature_interview_title": "मॉक इंटरव्यू अभ्यास",
        "feature_interview_desc": "लक्षित प्रश्नों का अभ्यास करें और अपने उत्तरों व संवाद कौशल पर प्रतिक्रिया पाएं।",
        "empty_analyses": "कोई विश्लेषण नहीं मिला। तैयारी शुरू करने के लिए अपना बायोडाटा अपलोड करें।",
        "empty_jobs": "अभी तक कोई आवेदन ट्रैक नहीं किया गया है।",
        "lang_switch_success": "भाषा सफलतापूर्वक बदल दी गई है।",
        "rate_limit_analyses_left": "इस घंटे {count} विश्लेषण शेष हैं",
        "rate_limit_evaluations_left": "इस घंटे {count} मूल्यांकन शेष हैं",
        "rate_limit_try_again": "आप {time} में पुनः प्रयास कर सकते हैं",
        "rate_limit_exceeded_title": "दर सीमा समाप्त"
    },
    "te": {
        "app_title": "ఏఐ క్యాంపస్ ప్లేస్‌మెంట్ ఏజెంట్",
        "tagline": "ఏఐ ఆధారిత క్యాంపస్ ప్లేస్‌మెంట్ ప్రిపరేషన్ సూట్",
        "nav_home": "హోమ్",
        "nav_login": "లాగిన్",
        "nav_register": "రిజిస్టర్",
        "nav_logout": "లాగౌట్",
        "nav_applications": "దరఖాస్తులు",
        "nav_history": "చరిత్ర",
        "nav_my_reports": "నా నివేదికలు",
        "nav_compare": "పోలిక వెర్షన్లు",
        "nav_internships": "ఇంటర్న్‌షిప్‌లను కనుగొనండి",
        "nav_coding": "కోడింగ్ ప్రాక్టీస్",
        "nav_interviews": "ఇంటర్వ్యూ చరిత్ర",
        "nav_settings": "నోటిఫికేషన్ సెట్టింగ్‌లు",
        "nav_delete_account": "ఖాతాను తొలగించండి",
        "btn_start_analysis": "సమగ్ర ప్లేస్‌మెంట్ విశ్లేషణను ప్రారంభించండి",
        "btn_upload_resume": "పిడిఎఫ్ / డాక్స్ అప్‌లోడ్ చేయండి",
        "btn_paste_text": "టెక్స్ట్ పేస్ట్ చేయండి",
        "btn_clear_form": "ఫారమ్ క్లియర్ చేయండి",
        "btn_download_pdf": "రిపోర్ట్ డౌన్‌లోడ్ చేసుకోండి",
        "label_select_company": "లక్ష్య సంస్థ",
        "label_job_description": "ఉద్యోగ వివరణ (ఐచ్ఛికం)",
        "label_role_title": "ఉద్యోగ పాత్ర శీర్షిక",
        "status_applied": "దరఖాస్తు చేయబడింది",
        "status_interview": "ఇంటర్వ్యూ",
        "status_selected": "ఎంపికయ్యారు",
        "status_rejected": "తిరస్కరించబడింది",
        "hero_heading": "మల్టీ-ఏజెంట్ ఏఐ తో మీ కలల ప్లేస్‌మెంట్‌ను సాధించండి",
        "hero_subheading": "ముఖ్యమైన సాంకేతిక కంపెనీల కోసం ఖచ్చితమైన ఏటీఎస్ స్కోరు, మాక్ ఇంటర్వ్యూలు మరియు రోడ్‌మ్యాప్‌లు.",
        "footer_copyright": "© 2026 ఏఐ క్యాంపస్ ప్లేస్‌మెంట్ ఏజెంట్. సర్వహక్కులు ప్రత్యేకించబడ్డాయి.",
        "empty_analyses": "విశ్లేషణలు కనుగొనబడలేదు. ప్రారంభించడానికి మీ రెజ్యూమ్‌ను అప్‌లోడ్ చేయండి.",
        "empty_jobs": "ఇంకా ఉద్యోగ దరఖాస్తులు ఏవీ ట్రాక్ చేయబడలేదు.",
        "lang_switch_success": "భాష విజయవంతంగా నవీకరించబడింది.",
        "rate_limit_analyses_left": "ఈ గంటలో {count} విశ్లేషణలు మిగిలి ఉన్నాయి",
        "rate_limit_evaluations_left": "ఈ గంటలో {count} మూల్యాంకనాలు మిగిలి ఉన్నాయి",
        "rate_limit_try_again": "మీరు {time} లో మళ్ళీ ప్రయత్నించవచ్చు",
        "rate_limit_exceeded_title": "రేట్ పరిమితి చేరుకుంది"
    },
    "es": {
        "app_title": "Agente de Colocación Campus IA",
        "tagline": "Preparación de Colocación Laboral con Inteligencia Artificial",
        "nav_home": "Inicio",
        "nav_login": "Iniciar Sesión",
        "nav_register": "Registrarse",
        "nav_logout": "Cerrar Sesión",
        "nav_applications": "Postulaciones",
        "nav_history": "Historial",
        "nav_my_reports": "Mis Informes",
        "nav_compare": "Comparar Versiones",
        "nav_internships": "Buscar Pasantías",
        "nav_coding": "Práctica de Código",
        "nav_interviews": "Historial de Entrevistas",
        "nav_settings": "Configuración de Notificaciones",
        "nav_delete_account": "Eliminar Cuenta",
        "btn_start_analysis": "Iniciar Análisis de Colocación",
        "btn_upload_resume": "Subir PDF / DOCX",
        "btn_paste_text": "Pegar Texto",
        "btn_clear_form": "Limpiar Formulario",
        "btn_download_pdf": "Descargar Informe Oficial",
        "label_select_company": "Empresa Objetivo",
        "label_job_description": "Descripción del Puesto (Opcional)",
        "label_role_title": "Puesto Objetivo",
        "status_applied": "Postulado",
        "status_interview": "Entrevista",
        "status_selected": "Seleccionado",
        "status_rejected": "Rechazado",
        "hero_heading": "Consigue el Empleo de tus Sueños con IA Multi-Agente",
        "hero_subheading": "Auditorías ATS en tiempo real, simulaciones de entrevistas dinámicas y mapas de aprendizaje personalizados.",
        "footer_copyright": "© 2026 Agente de Colocación Campus IA. Todos los derechos reservados.",
        "empty_analyses": "No se encontraron análisis. Sube tu currículum para comenzar.",
        "empty_jobs": "Aún no hay postulaciones registradas.",
        "lang_switch_success": "Idioma de interfaz actualizado exitosamente.",
        "rate_limit_analyses_left": "{count} análisis restantes esta hora",
        "rate_limit_evaluations_left": "{count} evaluaciones restantes esta hora",
        "rate_limit_try_again": "Puedes volver a intentarlo en {time}",
        "rate_limit_exceeded_title": "Límite de tasa alcanzado"
    }
}


def get_translation(key: str, locale: Optional[str] = None, default: Optional[str] = None, **kwargs) -> str:
    """
    Retrieves the translated text for a specified dictionary key and locale (#214).
    Falls back to English if key is missing in the chosen locale, and returns default if not in English.
    Supports interpolation with keyword arguments (e.g. count=5, time='2:35').
    """
    norm_locale = (locale or DEFAULT_LOCALE).strip().lower()
    if norm_locale not in TRANSLATION_CATALOG:
        norm_locale = DEFAULT_LOCALE

    # Check target locale
    locale_dict = TRANSLATION_CATALOG.get(norm_locale, {})
    fallback_dict = TRANSLATION_CATALOG.get(DEFAULT_LOCALE, {})

    if key in locale_dict:
        template_str = locale_dict[key]
    elif key in fallback_dict:
        template_str = fallback_dict[key]
    else:
        template_str = default if default is not None else key

    if kwargs:
        try:
            return template_str.format(**kwargs)
        except Exception:
            return template_str

    return template_str


def get_all_translations_for_locale(locale: Optional[str] = None) -> Dict[str, str]:
    """
    Returns the complete key-value catalog for client-side JavaScript or full-page templating.
    """
    norm_locale = (locale or DEFAULT_LOCALE).strip().lower()
    if norm_locale not in TRANSLATION_CATALOG:
        norm_locale = DEFAULT_LOCALE

    # Merge English fallback with target locale
    merged = dict(TRANSLATION_CATALOG[DEFAULT_LOCALE])
    merged.update(TRANSLATION_CATALOG.get(norm_locale, {}))
    return merged
