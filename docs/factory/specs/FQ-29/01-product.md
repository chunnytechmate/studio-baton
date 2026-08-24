# Gate 1 — Product

## The user problem

A teacher can finish a lesson and later move the learner on to another song.
When the teacher publishes the lesson record or sends the recap later, the song
details can follow the learner's newer assignment instead of the song that was
actually taught. The historical lesson then becomes misleading, and the learner
or parent may receive the wrong practice material.

The teacher needs each lesson to retain its own meaning after it is completed.
Changing what the learner studies next must not silently change an earlier
lesson.

## What success looks like

1. Once a lesson is completed, changing the learner's current song does not
   change the song title or learning links shown for that lesson.
2. Publishing immediately and publishing later produce the same song details
   for the same completed lesson.
3. Sending the recap later uses the same song details that the teacher reviewed
   in the lesson record.
4. If a song did not have an optional learning link at lesson time, the lesson
   does not borrow a link from another song and does not invent one.
5. Acceptance can be demonstrated entirely with fictional learners, songs, and
   lesson text; no real learner information is needed.

## Announcement as shipped

Lesson recaps now keep the song that was actually taught. You can change a
learner's current assignment after class without changing earlier lesson
records or sending practice links for the wrong song.

## Screens

No new screen or visual workflow is involved, so no mockup is required.

## What we are deliberately not doing

- Building a complete song-catalogue editor.
- Adding learner enrolment or student-management features.
- Choosing or recommending the learner's next song.
- Repairing or guessing song details for historical lessons that were already
  completed before this change.
- Changing transcription quality, lesson-summary writing style, or video
  processing.
- Adding a new messaging channel or changing who receives lesson recaps.
- Using real learner transcripts, recordings, videos, names, or contact details
  in automated acceptance tests.
