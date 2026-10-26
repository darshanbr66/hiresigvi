/** Starting points drawn from what the site actually offers. Kept to a few
 * that cover the most common visitor questions; anything else is one typed
 * question away. The chip shows the short `label`; picking it sends the full
 * `question` as the visitor's message, so the conversation reads naturally. */
export const SIGVI_SUGGESTIONS = [
  { label: "View open positions", question: "What positions are open right now?" },
  { label: "How do I apply?", question: "How do I apply?" },
  { label: "Campus opportunities", question: "What campus opportunities do you offer?" },
  { label: "Hiring process", question: "How does the hiring process work?" },
] as const;
