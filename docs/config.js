// Deployment settings of the upload page. Both values are public. While workerUrl is empty the page validates files in the browser and shows the
// other ways to submit (pull request, issue); it does not offer the one-click upload.
export const config = {
  workerUrl: "https://strata-bench-submit.strata-bench-submit.workers.dev",
  turnstileSiteKey: "0x4AAAAAAFUCFnJk8rjrVp9n",
};
