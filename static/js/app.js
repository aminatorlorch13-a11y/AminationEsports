(() => {
    "use strict";

    /*
     * ============================================================
     * FOUNDER HIGHLIGHT VIDEO UPLOAD
     * ============================================================
     */

    const fileInput = document.getElementById(
        "highlight-video-file"
    );

    const uploadButton = document.getElementById(
        "highlight-upload-button"
    );

    const statusElement = document.getElementById(
        "highlight-upload-status"
    );

    const videoUrlInput = document.getElementById(
        "highlight-video-url"
    );

    if (
        fileInput &&
        uploadButton &&
        statusElement &&
        videoUrlInput
    ) {
        let selectedFile = null;

        fileInput.addEventListener("change", () => {
            selectedFile = fileInput.files[0] || null;

            if (!selectedFile) {
                statusElement.textContent =
                    "Please choose a video first.";
                return;
            }

            statusElement.textContent =
                `Selected: ${selectedFile.name}. ` +
                "Tap UPLOAD VIDEO to continue.";
        });

        uploadButton.addEventListener(
            "click",
            async () => {
                const file =
                    selectedFile ||
                    fileInput.files[0] ||
                    null;

                if (!file) {
                    statusElement.textContent =
                        "Please choose a video first.";
                    return;
                }

                uploadButton.disabled = true;
                fileInput.disabled = true;

                statusElement.textContent =
                    "Uploading video... Please wait.";

                const formData = new FormData();
                formData.append("video", file);

                try {
                    const response = await fetch(
                        "/admin/founder/highlights/upload",
                        {
                            method: "POST",
                            body: formData,
                            credentials: "same-origin"
                        }
                    );

                    const contentType =
                        response.headers.get(
                            "content-type"
                        ) || "";

                    if (
                        !contentType.includes(
                            "application/json"
                        )
                    ) {
                        throw new Error(
                            "The server returned an unexpected response."
                        );
                    }

                    const data =
                        await response.json();

                    if (
                        !response.ok ||
                        !data.success
                    ) {
                        throw new Error(
                            data.error ||
                            "Video upload failed."
                        );
                    }

                    if (
                        typeof data.video_url !==
                            "string" ||
                        !data.video_url.trim()
                    ) {
                        throw new Error(
                            "The server did not return a valid video URL."
                        );
                    }

                    videoUrlInput.value =
                        data.video_url.trim();

                    statusElement.textContent =
                        "Video uploaded successfully. " +
                        "You can now publish the highlight.";

                } catch (error) {
                    statusElement.textContent =
                        error.message ||
                        "Video upload failed.";

                    uploadButton.disabled = false;
                    fileInput.disabled = false;
                }
            }
        );
    }


    /*
     * ============================================================
     * PUBLIC HIGHLIGHT VIEW ANALYTICS
     * ============================================================
     */

    const highlightVideos = document.querySelectorAll(
        "video[data-highlight-id]"
    );

    highlightVideos.forEach((video) => {
        video.addEventListener(
            "play",
            async () => {
                if (
                    video.dataset.analyticsTracked ===
                    "1"
                ) {
                    return;
                }

                const highlightId =
                    Number(
                        video.dataset.highlightId
                    );

                if (
                    !Number.isInteger(highlightId) ||
                    highlightId <= 0
                ) {
                    return;
                }

                video.dataset.analyticsTracked =
                    "1";

                try {
                    const response =
                        await fetch(
                            "/analytics/highlight-view",
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type":
                                        "application/json"
                                },
                                credentials:
                                    "same-origin",
                                body: JSON.stringify({
                                    highlight_id:
                                        highlightId
                                })
                            }
                        );

                    if (!response.ok) {
                        video.dataset.analyticsTracked =
                            "0";
                    }
                } catch (error) {
                    video.dataset.analyticsTracked =
                        "0";
                }
            },
            { passive: true }
        );
    });
})();
