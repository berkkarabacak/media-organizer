// ============================================================
// Site Configuration — Media Organizer
// ============================================================

export interface SiteConfig {
  language: string;
  brandName: string;
}

export const siteConfig: SiteConfig = {
  language: "en",
  brandName: "Media Organizer",
};

// ============================================================
// Navigation
// ============================================================

export interface NavLink {
  label: string;
  href: string;
}

export interface NavigationConfig {
  links: NavLink[];
  ctaText: string;
}

export const navigationConfig: NavigationConfig = {
  links: [
    { label: "The Problem", href: "#cinematic" },
    { label: "How It Works", href: "#how-it-works" },
    { label: "Features", href: "#curriculum" },
    { label: "Screenshots", href: "#screenshots" },
    { label: "Pricing", href: "#pricing" },
    { label: "FAQ", href: "#faq" },
  ],
  ctaText: "Download",
};

// ============================================================
// Hero
// ============================================================

export interface HeroConfig {
  title: string;
  subtitleLine1: string;
  subtitleLine2: string;
  ctaText: string;
  ctaNote: string;
}

export const heroConfig: HeroConfig = {
  title: "Memories, in order.",
  subtitleLine1: "Auto-sorts photos & videos by true capture date, from EXIF.",
  subtitleLine2: "Dry-run preview. Duplicate detection. One-click undo.",
  ctaText: "Download for Windows",
  ctaNote: "Windows may show a SmartScreen prompt for new apps — click “More info” → “Run anyway”. The app is 100% offline and fully open source.",
};

// ============================================================
// Capabilities (Curriculum section)
// ============================================================

export interface CapabilityItem {
  title: string;
  slug: string;
  description: string;
  image: string;
}

export interface CapabilitiesConfig {
  sectionLabel: string;
  items: CapabilityItem[];
}

export const capabilitiesConfig: CapabilitiesConfig = {
  sectionLabel: "Core Features",
  items: [
    {
      title: "True Capture Dates",
      slug: "capture-dates",
      description:
        "Reads the real moment a photo or video was taken from EXIF and video container metadata — never the misleading file copy date.",
      image: "images/capability-1.jpg",
    },
    {
      title: "Preview Before Anything",
      slug: "dry-run-preview",
      description:
        "Every run starts as a dry run. Review the complete folder plan — every source, every destination — before a single file is touched.",
      image: "images/capability-2.jpg",
    },
    {
      title: "Duplicates, Detected",
      slug: "duplicate-detection",
      description:
        "SHA-256 hashing finds exact duplicates across your whole library, so years of copied backups collapse into one clean archive.",
      image: "images/capability-3.jpg",
    },
    {
      title: "Undo, Anytime",
      slug: "undo",
      description:
        "Every organize run is logged. Made a mistake or changed your mind? Restore your original folder structure in one click.",
      image: "images/capability-4.jpg",
    },
  ],
};

// ============================================================
// Capability Detail (sub-pages)
// ============================================================

export interface CapabilityDetailData {
  title: string;
  subtitle: string;
  paragraphs: string[];
}

export interface CapabilityDetailConfig {
  sectionLabel: string;
  backLinkText: string;
  prevLabel: string;
  nextLabel: string;
  notFoundText: string;
  capabilities: Record<string, CapabilityDetailData>;
}

export const capabilityDetailConfig: CapabilityDetailConfig = {
  sectionLabel: "Feature",
  backLinkText: "Back to home",
  prevLabel: "Previous",
  nextLabel: "Next",
  notFoundText: "Feature not found.",
  capabilities: {
    "capture-dates": {
      title: "True Capture Dates",
      subtitle: "The date the moment happened — not the date Windows copied it.",
      paragraphs: [
        "File copy dates lie. Every time you move photos between drives, phones, or cloud folders, Windows stamps them with a fresh date — and suddenly a decade of memories all look like they were taken last Tuesday. Media Organizer ignores file timestamps entirely and goes straight to the source: the EXIF metadata embedded in every photo and the creation metadata inside every video container.",
        "For photos, it reads DateTimeOriginal and related EXIF fields. For videos — MP4, MOV, AVI, MKV and more — it parses the container's own creation-time fields. The result is the actual moment the shutter fired or the recording started, accurate to the second.",
        "When a file has no embedded metadata at all, Media Organizer falls back to detecting dates in the filename itself — patterns like IMG_20240315_, VID-2023-12-31, or Screenshot 2022-08-04 are recognized automatically. Only files with truly no date signal are left for you to review.",
      ],
    },
    "dry-run-preview": {
      title: "Preview Before Anything",
      subtitle: "See the entire plan before a single file moves.",
      paragraphs: [
        "Organizing ten thousand files is scary when you can't see what's about to happen. That's why every Media Organizer run starts as a dry run: it scans your folder, reads every date, and builds a complete plan — source path, detected date, and exact destination for every file — without touching anything.",
        "The preview shows you the proposed year/quarter/month folder tree, flags files with missing dates, and lists duplicates it found along the way. You stay in control: approve the plan, adjust the rules, or walk away with everything exactly as it was.",
        "Only when you explicitly confirm does the organizer execute — in copy mode, which leaves originals in place, or move mode, which relocates files into the new structure. Either way, nothing happens silently in the background.",
      ],
    },
    "duplicate-detection": {
      title: "Duplicates, Detected",
      subtitle: "Cryptographic hashing, not filename guessing.",
      paragraphs: [
        "Years of backups, phone exports, and 'final_final' folders mean most libraries contain the same files two, three, or five times over. Media Organizer computes a SHA-256 hash of every file's actual contents — so two files are only called duplicates when they are byte-for-byte identical, regardless of name or location.",
        "Duplicates are grouped and shown in the preview before anything happens, with their sizes and locations side by side. You decide whether to keep one organized copy and skip the rest, or keep everything. Reclaimed space is reported at the end of every run.",
        "Hashing runs fully offline on your own machine, with progress you can watch in real time. Large libraries are processed incrementally, so even a terabyte archive stays responsive.",
      ],
    },
    undo: {
      title: "Undo, Anytime",
      subtitle: "Every run is reversible — down to the last file.",
      paragraphs: [
        "Trust matters when a tool touches your memories. Media Organizer writes a complete operation log for every organize run: which files moved, from where, to where, and in what order. That log is your safety net.",
        "Changed your mind about a run — five minutes later or five days later? Open the history, pick the run, and hit undo. Every file is returned to its original location and the new folder structure is cleaned up, leaving your drive exactly as it was before.",
        "Undo works for both copy and move modes, and it validates each file before restoring it, so a half-finished or interrupted run can still be rolled back safely. Organizing stops being a leap of faith.",
      ],
    },
  },
};

// ============================================================
// Architecture (CinematicVision section) — used as "The Problem"
// ============================================================

export interface ArchitectureConfig {
  sectionLabel: string;
  videoPath: string;
  title: string;
  description: string;
}

export const architectureConfig: ArchitectureConfig = {
  sectionLabel: "The Problem",
  videoPath: "",
  title: "Scattered folders. Broken dates. Lost moments.",
  description:
    "Photos from phones, cameras, drones and chat apps pile up in one giant folder. Every copy between drives rewrites the file dates, so sorting by date stops meaning anything. The memories are all still there — finding the one you want is the hard part.",
};

// ============================================================
// Research (AlumniArchives section) — used as "And everything else"
// ============================================================

export interface ResearchProject {
  title: string;
  year: string;
  discipline: string;
  image: string;
}

export interface ResearchConfig {
  sectionLabel: string;
  projects: ResearchProject[];
}

export const researchConfig: ResearchConfig = {
  sectionLabel: "And Everything Else",
  projects: [
    {
      title: "7 Folder Strategies",
      year: "New",
      discipline: "Folder Structure",
      image: "images/research-1.jpg",
    },
    {
      title: "Sort by Location (GPS)",
      year: "New",
      discipline: "Geotagging",
      image: "images/research-2.jpg",
    },
    {
      title: "Filename Date Fallback",
      year: "Fallback",
      discipline: "Date Detection",
      image: "images/research-3.jpg",
    },
    {
      title: "100% Offline & Private",
      year: "No upload",
      discipline: "Privacy",
      image: "images/research-4.jpg",
    },
    {
      title: "Progress Bar + ETA",
      year: "Real-time",
      discipline: "Feedback",
      image: "images/research-1.jpg",
    },
    {
      title: "Guided 3-Step Flow",
      year: "Simple",
      discipline: "Design",
      image: "images/research-2.jpg",
    },
    {
      title: "HEIC, RAW & 4K Video",
      year: "Wide",
      discipline: "Format Support",
      image: "images/research-3.jpg",
    },
    {
      title: "Libraries of Any Size",
      year: "Fast",
      discipline: "Performance",
      image: "images/research-4.jpg",
    },
  ],
};

// ============================================================
// Footer
// ============================================================

export interface FooterLinkColumn {
  title: string;
  links: string[];
}

export interface FooterBottomLink {
  label: string;
  href: string;
}

export interface FooterConfig {
  heading: string;
  columns: FooterLinkColumn[];
  copyright: string;
  bottomLinks: FooterBottomLink[];
}

export const footerConfig: FooterConfig = {
  heading: "Your memories, back in order.",
  columns: [
    {
      title: "Product",
      links: ["Download", "Features", "Pricing", "Changelog"],
    },
    {
      title: "Support",
      links: ["FAQ", "User Guide", "Contact", "Refund Policy"],
    },
  ],
  copyright: "© 2026 Media Organizer. All rights reserved. (Placeholder)",
  bottomLinks: [
    { label: "Privacy Policy", href: "#" },
    { label: "Terms of Use", href: "#" },
    { label: "EULA", href: "#" },
    { label: "Code signing policy", href: "https://github.com/berkkarabacak/media-organizer#code-signing-policy" },
  ],
};
