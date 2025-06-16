from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.models.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    full_name = Column(String, nullable=True)
    is_tutor = Column(Boolean, default=False)
    tutor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # for students

    students = relationship("User", cascade="save-update, merge", backref="tutor", remote_side=[id])
    lessons = relationship("Lesson", cascade="all, delete-orphan", back_populates="tutor", foreign_keys="Lesson.tutor_id")
    lessons_as_student = relationship("Lesson", cascade="all, delete-orphan", back_populates="student", foreign_keys="Lesson.student_id")


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True)
    tutor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))

    time = Column(DateTime(timezone=True), nullable=False)
    repeat_interval = Column(String(10), nullable=True)  # "daily" | "weekly" | None
    series_id = Column(Integer, ForeignKey("lessons.id"), nullable=True)
    is_canceled = Column(Boolean, default=False)

    tutor = relationship("User", back_populates="lessons", foreign_keys=[tutor_id])
    student = relationship("User", back_populates="lessons_as_student", foreign_keys=[student_id])


class Homework(Base):
    __tablename__ = "homework"

    id = Column(Integer, primary_key=True)
    tutor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))

    text = Column(String, default="")
    files = Column(JSON, default=list)  # list[str]

    answer_text = Column(String, nullable=True)
    answer_files = Column(JSON, nullable=True)

    assigned_at = Column(DateTime, default=datetime.utcnow)
    answered_at = Column(DateTime, nullable=True)
    checked = Column(Boolean, default=False)