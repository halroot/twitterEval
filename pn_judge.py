#coding:utf-8
import re
import sys
import MeCab
import inspect
import numpy as np
import datetime as dt
from twitter import *

class Morpheme(object):
	"""形態素の情報を格納するクラス
	"""
	def __init__(self, word, part, conj, conj_type, origin):
		""" pn_polarityは感情極性(<float>)を表す．
		Argv:
		    word: <str> 単語
			part: <str> 品詞
			conj: <str> 活用形
			conj_type: <str> 活用型
			origin:	<str> 原形
		"""
		self.word = word
		self.part_of_speech = part
		self.conjugation = conj
		self.conjugation_type = conj_type
		self.original_form = origin
		if origin != "*":
			self.pn_polarity = PNDICT.get((origin, part), float(0))
		else:
			self.pn_polarity = PNDICT.get((word, part), float(0))

		self.prev = None

	def __str__(self):
		"""　インスタンス変数の文字列表現を返す．
		"""
		ans = "+++++object members+++++\n"
		member_self = inspect.getmembers(self)
		for name, value in member_self:
		    if name[:2] != "_" and name[:2] != "__" and (isinstance(value, float) or isinstance(value, str)):
		        ans += name + ": {}\n".format(value)
		    elif isinstance(value, Morpheme):
		    	ans += name + ": {}\n".format(value.word)
		return ans


class Evaluate(MeCab.Tagger):
	"""　テキストの極性値評価を行うクラス．
	"""

	def __init__(self, args='-Ochasen'):
		super().__init__(args)

	def _sentence2morpheme(self, sentence):
		""" 文字列を解析して，形態素クラスを格納したリストを返す．
		Argv:
			sentence: <str>	被解析文字列
		Return:
		    <list> 形態素クラスを格納したリスト
		"""
		morpheme_list = []
		self.parse("")
		node = self.parseToNode(sentence)

		while node:
			ft = node.feature.split(",")
			if ft[0] == "BOS/EOS" or ft[0] == "記号":
				node = node.next
				continue
			morpheme = Morpheme(word=node.surface, part=ft[0], conj=ft[4], conj_type=ft[5], origin=ft[6])
			# print(morpheme)
			if len(morpheme_list) > 0:
				morpheme.prev = morpheme_list[-1]
			morpheme_list.append(morpheme)
			node = node.next

		return morpheme_list

	def negaposi(self, text):
		""" テキストに対してネガポジ判定を行う．
		Argv:
			text: <str>	文字列
		Return:
		    <float> 極性スコア
		"""
		sentences = list(filter(lambda x: len(x) > 0 and not x.startswith("http"), re.split(r'\s+|。|．|？', text)))
		pn = []
		for sentence in sentences:
			mlist = self._sentence2morpheme(sentence=sentence)

			#動詞/形容詞の直後に強い否定or肯定が続いたとき，動詞/形容詞の極性を0にする
			for i in mlist:
				if i.prev and abs(i.pn_polarity) > 0.9 and i.prev.part_of_speech in ("動詞", "形容詞"):
					i.prev.pn_polarity = 0.0
				# print(i)

			#極性値がニュートラルな形態素をカット
			mlist_filtered = [i for i in mlist if (abs(i.pn_polarity) > 0.8 and i.part_of_speech != "名詞") or abs(i.pn_polarity) > 0.5]
			
			pnlist = list(map(lambda x: x.pn_polarity, mlist_filtered))
			wordlist = list(map(lambda x: x.word, mlist_filtered))

			if len(pnlist) > 0:
				pn_per_sentence = np.array(pnlist).mean()
			else:
				pn_per_sentence = None

			pn.extend(pnlist)
			print("*********************")
			print("-----sentence: {}".format(sentence))
			print("-----pn list: {}".format(pnlist))
			print("-----selected words: {}".format(wordlist))
			print("-----score per sentence: {}".format(pn_per_sentence))
		print("*********************")
		if len(pn) > 0:
			return np.array(pn).mean()
		else:
			return 0


class TwitterEval(Evaluate):
	"""TwitterのSearchAPIを叩いてテキストを取得するクラス，ついでに評価も行う．
		API制限の緩いApplication-only authenticationを利用．
	"""
	def __init__(self, args='-Ochasen', pickup_count=20):
		""" 
		Argv:
		    args: <str> MeCabのモード
			pickup_count: <int> 取得するツイート数
		"""
		super().__init__(args)
		Consumerkey = "3A4gOYiRUpgQzoEwcWrVW6NyW"
		Consumersecret = "1Mnoerx5G8xjs73XRCGrORWibrQIjVR3BpHjRJqRICMcNcSEUA"

		BEARER_TOKEN = oauth2_dance(Consumerkey, Consumersecret)
		self.api = Twitter(auth=OAuth2(bearer_token=BEARER_TOKEN))
		self.params = {"count":None, "q":None, "lang":"ja", "result_type":"recent"}

		#countが100を超えた場合はリストに分割する．
		self._count_list = []
		if pickup_count < 0:
			print("The count is negative value.")
			sys.exit()
		elif pickup_count <= 100:
			self._count_list.append(pickup_count)
		else:
			self._count_list.extend([100] * (pickup_count / 100))
			if pickup_count % 100 != 0:
				self._count_list.append(pickup_count % 100)

	def print_limit(self):
		""" SearchAPIの制限回数，次回リセット時刻を表示．
		"""
		limit = self.api.application.rate_limit_status(resources="search")["resources"]["search"]['/search/tweets']
		print("Remaining times: {}".format(limit["remaining"]))
		print("Next reset: {}".format(dt.datetime.fromtimestamp(limit["reset"])))

	def search_(self, word):
		""" Twitter検索してネガポジを計算．
		Argv:
		    word: <str> 検索クエリ
		Return:
		    None
		"""
		self.params["q"] = word

		#1度のGET数に制限があるため，max_idを更新することで解決
		for i in self._count_list:
		    self.params["count"] = i
		    get = self.api.search.tweets(**self.params)
		    g = get["statuses"]
		    for j in g:
		    	# if not j["in_reply_to_screen_name"]: リプライを考慮する場合
		        if "retweeted_status" in j:
		        	target_text = j["retweeted_status"]["text"]
		        	retweeted_count = j["retweeted_status"]["retweet_count"]
		        else:
		            target_text = j["text"]
		            retweeted_count = j["retweet_count"]

		        print("\n======================================")
		        print("text: {}".format(target_text))
		        print("retweeted count: {}".format(retweeted_count))
		        print("pn value: {}".format(self.negaposi(target_text)))
		    self.params["max_id"] = j["id_str"]


def read_pn_text(fname="pn_ja.txt"):
	""" 単語感情極性表を辞書形式に変換する．
	Argv:
		fname: <str> 極性表のファイル名
	Return:
	    <dict> key:単語，value:極性値
	"""
	res = {}
	with open(fname, "r") as f:
		line = f.readline()
		while line:
			l = line.split(":")
			key = (l[0], l[2])
			val = float(l[3])
			res[key] = val
			line = f.readline()
	return res

PNDICT = read_pn_text()

if __name__ == '__main__':
	TwitterEval(pickup_count=5).search_("甲子園")
