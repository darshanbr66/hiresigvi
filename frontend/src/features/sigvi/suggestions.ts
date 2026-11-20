/** Starting points drawn from what the site actually offers. Kept to a few
 * that cover the most common visitor questions; anything else is one typed
 * question away. The chip shows the short `label`; picking it sends the full
 * `question` as the visitor's message, so the conversation reads naturally. */
export const SIGVI_SUGGESTIONS = [
  { label: "View open positions", question: "What positions are open right now?" },
  { label: "Internships & training", question: "What internships and training programs are available?" },
  { label: "What does Zodiac Patent do?", question: "What kind of work does Zodiac Patent do?" },
  { label: "How do I apply?", question: "How do I apply?" },
] as const;
