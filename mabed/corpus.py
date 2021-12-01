# coding: utf-8

# std
import string
from datetime import timedelta, datetime
import csv
import os
import shutil
import pickle

# math
import numpy as np
from scipy.sparse import *
from scipy.sparse.dok import dok_matrix

# mabed
import mabed.utils as utils

# nlp
import spacy

__authors__ = "Adrien Guille, Nicolas Dugué"
__email__ = "adrien.guille@univ-lyon2.fr"

# DATETIME_FORMAT="%Y-%m-%d %H:%M:%S"
DATETIME_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT_LENGTH = len(DATETIME_FORMAT.replace('%Y', '1234'))


class Corpus:

    def __init__(self, source_file_path, stopwords_file_path, min_absolute_freq=10, max_relative_freq=0.4, separator='\t', save_voc=False):
        self.source_file_path = source_file_path
        self.size = 0
        self.start_date = '3000-01-01 00:00:00'[:DATETIME_FORMAT_LENGTH]
        self.end_date = '1970-01-01 00:00:00'[:DATETIME_FORMAT_LENGTH]
        self.min_date = '1970-01-01 00:00:00'[:DATETIME_FORMAT_LENGTH]
        self.separator = separator

        # load stop-words
        self.stopwords = utils.load_stopwords(stopwords_file_path)

        # identify features
        word_frequency = {}
        for (date, text) in self.source_csv_iterator():
            self.size += 1

            words = self.tokenize(text)

            if date > self.end_date:
                self.end_date = date
            elif date < self.start_date:
                self.start_date = date

            # update word frequency
            for word in words:
                if len(word) > 1:
                    frequency = word_frequency.get(word)
                    if frequency is None:
                        frequency = 0
                    word_frequency[word] = frequency + 1
        # sort words w.r.t frequency
        vocabulary = list(word_frequency.items())
        vocabulary.sort(key=lambda x: x[1], reverse=True)
        if save_voc:
            with open('vocabulary.pickle', 'wb') as output_file:
                pickle.dump(vocabulary, output_file)
        self.vocabulary = {}
        vocabulary_size = 0
        # construct the vocabulary map
        for word, frequency in vocabulary:
            if frequency > min_absolute_freq and float(frequency / self.size) < max_relative_freq and word not in self.stopwords:
                self.vocabulary[word] = vocabulary_size
                vocabulary_size += 1
        self.start_date = datetime.strptime(self.start_date, DATETIME_FORMAT)
        self.end_date = datetime.strptime(self.end_date, DATETIME_FORMAT)
        print('   Corpus: %i tweets, spanning from %s to %s' % (self.size,
                                                                self.start_date,
                                                                self.end_date))
        print('   Vocabulary: %d distinct words' % vocabulary_size)
        self.time_slice_count = None
        self.tweet_count = None
        self.global_freq = None
        self.mention_freq = None
        self.time_slice_length = None

    def source_csv_iterator(self):
        with open(self.source_file_path, 'r') as input_file:
            csv_reader = csv.reader(input_file, delimiter=self.separator)
            header = next(csv_reader)
            text_column_index = header.index('text')
            date_column_index = header.index('date')

            for line in csv_reader:
                # if len(line) != 4:
                #     print('skipping line:', line)
                #     continue
                date = line[date_column_index]
                text = line[text_column_index]

                if date < self.min_date:
                    print('skipping line:', line)
                    continue  # ignore

                yield date, text

    def discretize(self, time_slice_length):
        self.time_slice_length = time_slice_length

        nlp = spacy.load("en_core_web_sm")

        # clean the data directory
        if os.path.exists('corpus'):
            shutil.rmtree('corpus')
        os.makedirs('corpus')

        # compute the total number of time-slices
        time_delta = (self.end_date - self.start_date)
        time_delta = time_delta.total_seconds()/60
        self.time_slice_count = int(time_delta // self.time_slice_length) + 1
        self.tweet_count = np.zeros(self.time_slice_count)
        print('   Number of time-slices: %d' % self.time_slice_count)

        # create empty files
        for time_slice in range(self.time_slice_count):
            dummy_file = open('corpus/' + str(time_slice), 'w')
            dummy_file.write('')

        # compute word frequency
        self.global_freq = dok_matrix(
            (len(self.vocabulary), self.time_slice_count), dtype=np.uint32)
        self.mention_freq = dok_matrix(
            (len(self.vocabulary), self.time_slice_count), dtype=np.uint32)

        my_index = 0
        for (date, text) in self.source_csv_iterator():
            my_index += 1
            if my_index % 1000 == 0:
                print('*** current line:', my_index)

            tweet_date = datetime.strptime(date, DATETIME_FORMAT)
            time_delta = (tweet_date - self.start_date)
            time_delta = time_delta.total_seconds() / 60
            time_slice = int(time_delta / self.time_slice_length)
            self.tweet_count[time_slice] += 1
            # tokenize the tweet and update word frequency
            tweet_text = text
            words = self.tokenize(tweet_text)

            # mention = '@' in tweet_text
            # mention = 'Apple' in tweet_text

            nlp_text = nlp(tweet_text)

            # propnouns = filter(lambda t: t.pos_ == 'PROPN', nlp_text)
            # has_propnouns = any(propnouns)

            orgs = filter(lambda t: t.ent_type_ ==
                          'ORG' and len(t.text) > 1, nlp_text)
            # At least 2 organizations mentionned
            has_orgs = any(orgs) and any(orgs)

            mention = has_orgs

            # if mention:
            #     print(tweet_text)

            for word in set(words):
                word_id = self.vocabulary.get(word)
                if word_id is not None:
                    self.global_freq[word_id, time_slice] += 1
                    if mention:
                        self.mention_freq[word_id, time_slice] += 1
            with open('corpus/' + str(time_slice), 'a') as time_slice_file:
                time_slice_file.write(tweet_text+'\n')
        self.global_freq = self.global_freq.tocsr()
        self.mention_freq = self.mention_freq.tocsr()

    def to_date(self, time_slice):
        a_date = self.start_date + timedelta(
            minutes=time_slice * self.time_slice_length)
        return a_date

    def tokenize(self, text):
        # split the documents into tokens based on whitespaces
        raw_tokens = text.split()
        # trim punctuation and convert to lower case
        return [token.strip(string.punctuation).lower() for token in raw_tokens if len(token) > 1 and 'http' not in token]

    def cooccurring_words(self, event, p):
        main_word = event[2]
        word_frequency = {}
        for i in range(event[1][0], event[1][1] + 1):
            with open('corpus/' + str(i), 'r') as input_file:
                for tweet_text in input_file.readlines():
                    words = self.tokenize(tweet_text)
                    if event[2] in words:
                        for word in words:
                            if word != main_word:
                                if len(word) > 1 and self.vocabulary.get(word) is not None:
                                    frequency = word_frequency.get(word)
                                    if frequency is None:
                                        frequency = 0
                                    word_frequency[word] = frequency + 1
        # sort words w.r.t frequency
        vocabulary = list(word_frequency.items())
        vocabulary.sort(key=lambda x: x[1], reverse=True)
        top_cooccurring_words = []
        for word, frequency in vocabulary:
            top_cooccurring_words.append(word)
            if len(top_cooccurring_words) == p:
                # return the p words that co-occur the most with the main word
                return top_cooccurring_words