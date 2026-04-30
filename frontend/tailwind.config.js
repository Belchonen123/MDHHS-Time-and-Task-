import tailwindcssAnimate from "tailwindcss-animate"
import typography from "@tailwindcss/typography"

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    // Override Tailwind's default type scale to match our 8pt grid.
    fontSize: {
      xs:   ["12px", { lineHeight: "16px" }],
      sm:   ["14px", { lineHeight: "20px" }],
      base: ["15px", { lineHeight: "24px" }],
      lg:   ["17px", { lineHeight: "26px" }],
      xl:   ["20px", { lineHeight: "28px" }],
      "2xl": ["24px", { lineHeight: "32px" }],
      "3xl": ["30px", { lineHeight: "38px" }],
      "4xl": ["36px", { lineHeight: "44px" }],
      "5xl": ["48px", { lineHeight: "56px" }],
    },
    letterSpacing: {
      tighter: "-0.03em",
      tight:   "-0.02em",
      normal:  "0",
      wide:    "0.025em",
      wider:   "0.05em",
      widest:  "0.1em",
    },
    // Kill Tailwind's default shadows so `shadow-md` etc. always route to our tokens.
    boxShadow: {
      none:  "none",
      xs:    "var(--shadow-xs)",
      sm:    "var(--shadow-sm)",
      DEFAULT: "var(--shadow-sm)",
      md:    "var(--shadow-md)",
      lg:    "var(--shadow-lg)",
      xl:    "var(--shadow-xl)",
      focus: "var(--shadow-focus)",
    },
    extend: {
      fontFamily: {
        sans:    ["var(--font-sans)"],
        mono:    ["var(--font-mono)"],
        display: ["var(--font-display)"],
      },
      colors: {
        // shadcn/ui semantic tokens — preserved so existing components still work.
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },

        // Design-system primary (navy) — shadcn's `primary` points at the 700 shade
        // so `bg-primary` matches our dominant color.
        primary: {
          DEFAULT: "var(--primary-700)",
          foreground: "#FFFFFF",
          50:  "var(--primary-50)",
          100: "var(--primary-100)",
          200: "var(--primary-200)",
          300: "var(--primary-300)",
          400: "var(--primary-400)",
          500: "var(--primary-500)",
          600: "var(--primary-600)",
          700: "var(--primary-700)",
          800: "var(--primary-800)",
          900: "var(--primary-900)",
        },

        // Warm-gray neutrals.
        neutral: {
          50:  "var(--neutral-50)",
          100: "var(--neutral-100)",
          200: "var(--neutral-200)",
          300: "var(--neutral-300)",
          400: "var(--neutral-400)",
          500: "var(--neutral-500)",
          600: "var(--neutral-600)",
          700: "var(--neutral-700)",
          800: "var(--neutral-800)",
          900: "var(--neutral-900)",
        },

        // Semantic.
        success: {
          DEFAULT: "var(--success)",
          bg:      "var(--success-bg)",
        },
        warning: {
          DEFAULT: "var(--warning)",
          bg:      "var(--warning-bg)",
        },
        danger: {
          DEFAULT: "var(--danger)",
          bg:      "var(--danger-bg)",
        },
        info: {
          DEFAULT: "var(--info)",
          bg:      "var(--info-bg)",
        },
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius)",
        md: "var(--radius)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
      },
      transitionTimingFunction: {
        "out-soft": "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      transitionDuration: {
        fast: "180ms",
        base: "240ms",
        slow: "400ms",
      },
    },
  },
  plugins: [tailwindcssAnimate, typography],
}
